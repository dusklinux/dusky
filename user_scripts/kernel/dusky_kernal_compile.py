#!/usr/bin/env python3
"""Dusky Kernel Compiler v6.0.0 -- bespoke kernel compilation engine for Arch Linux.

Target stack (no legacy paths exist in this program):
  * Arch Linux rolling (September 2026 spec), Linux 7.2+ and 7.3-rc, Python 3.14+
  * LLVM/Clang 21+ (ThinLTO with persistent disk cache, monolithic full LTO, kCFI, AutoFDO/Propeller)
  * Rust-for-Linux (scripts/rustavailable validation), sched_ext BPF schedulers, Cache-Aware Scheduling
  * PREEMPT_LAZY / PREEMPT_DYNAMIC, AMD P-State EPP autonomy, in-tree NTSync, ZRAM multi-compression

Pipeline (one profile -> two pacman packages):
  profile.toml -> host telemetry -> release selection (kernel.org releases.json) -> tarball + SHA256/PGP
  -> source tree (per patch-set) -> scheduler patches (BORE/BMQ) -> Kconfig.hz injection (500/600/750 Hz)
  -> seed .config (snapshot | Arch upstream config | /proc/config.gz | headers | defconfig)
  -> olddefconfig -> localmodconfig(LSMOD=modprobed.db, strict|expanded) -> Kconfig index scan
  -> declarative Kconfig matrix (batched scripts/config) -> olddefconfig -> contract verification
  -> make pacman-pkg (linux-dusky-<flavor> + linux-dusky-<flavor>-headers) -> pacman -U
  -> bootloader refresh (systemd-boot entries, GRUB, rEFInd, Limine, kernel-install)

Quick start:
  ./dusky_kernal_compile.py --write-default-profiles
  ./dusky_kernal_compile.py --doctor
  ./dusky_kernal_compile.py --profile low_ram             # asks: use defaults exactly? [Y/n]
  ./dusky_kernal_compile.py --profile gaming --wizard      # force the granular questionnaire
  ./dusky_kernal_compile.py --profile gaming --configure-only --print-matrix
"""

import sys

if sys.version_info < (3, 14):
    sys.stderr.write(f"Dusky Kernel Compiler requires Python >= 3.14 (running {sys.version.split()[0]}).\n")
    raise SystemExit(70)

import argparse
import collections
import functools
import hashlib
import itertools
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import tarfile
import tempfile
import textwrap
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal, Self

type Sections = dict[str, dict[str, Any]]
type Json = dict[str, Any]

# ---------------------------------------------------------------------------------------------------
# Constants & filesystem layout (XDG aware, env-overridable)
# ---------------------------------------------------------------------------------------------------
APP_NAME: Final = "Dusky Kernel Compiler"
APP_VERSION: Final = "6.0.0"
APP_TAGLINE: Final = "Tailored Arch Linux kernels (Linux 7.2+ / 7.3-rc)"
MIN_KERNEL: Final = (7, 2)
SCRIPT_DIR: Final = Path(__file__).resolve().parent
XDG_CONFIG: Final = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
XDG_CACHE: Final = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
XDG_STATE: Final = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
PROFILES_DIR: Final = Path(os.environ.get("DUSKY_PROFILES_DIR") or SCRIPT_DIR / "kernel_profiles")
USER_PROFILES_DIR: Final = XDG_CONFIG / "dusky-kernel" / "kernel_profiles"
CONFIG_SNAPSHOT_DIR: Final = XDG_CONFIG / "dusky-kernel" / "configs"
STATE_DIR: Final = XDG_STATE / "dusky-kernel"


def _detect_default_build_dir() -> Path:
    env = os.environ.get("DUSKY_BUILD_DIR")
    if env:
        return Path(env).expanduser()
    zram = Path("/mnt/zram1/dusky_kernel")
    if Path("/mnt/zram1").is_dir():
        return zram
    return XDG_CACHE / "dusky-kernel"


BUILD_DIR: Path = _detect_default_build_dir()
SRC_DIR: Path = BUILD_DIR / "src"
TARBALL_DIR: Path = BUILD_DIR / "tarballs"
PATCH_CACHE: Path = Path(os.environ.get("DUSKY_PATCH_CACHE") or BUILD_DIR / "patches")
THINLTO_CACHE_DIR: Path = Path(os.environ.get("DUSKY_THINLTO_CACHE") or BUILD_DIR / "thinlto-cache")
PKGDEST_DIR: Path = Path(os.environ.get("DUSKY_PKGDEST") or BUILD_DIR / "packages")
IMPORT_DIR: Path = BUILD_DIR / "imports"


def set_build_dir(new_path: Path | str) -> None:
    global BUILD_DIR, SRC_DIR, TARBALL_DIR, PATCH_CACHE, THINLTO_CACHE_DIR, PKGDEST_DIR, IMPORT_DIR
    BUILD_DIR = Path(new_path).expanduser()
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    SRC_DIR = BUILD_DIR / "src"
    TARBALL_DIR = BUILD_DIR / "tarballs"
    PATCH_CACHE = Path(os.environ.get("DUSKY_PATCH_CACHE") or BUILD_DIR / "patches")
    THINLTO_CACHE_DIR = Path(os.environ.get("DUSKY_THINLTO_CACHE") or BUILD_DIR / "thinlto-cache")
    PKGDEST_DIR = Path(os.environ.get("DUSKY_PKGDEST") or BUILD_DIR / "packages")
    IMPORT_DIR = BUILD_DIR / "imports"
    for d in (SRC_DIR, TARBALL_DIR, PATCH_CACHE, THINLTO_CACHE_DIR, PKGDEST_DIR, IMPORT_DIR, BUILD_DIR / "seeds"):
        d.mkdir(parents=True, exist_ok=True)
LOG_DIR: Final = STATE_DIR / "logs"
HISTORY_FILE: Final = STATE_DIR / "history.json"
MODPROBED_DB_PATH: Final = XDG_CONFIG / "modprobed.db"
KERNEL_ORG_RELEASES: Final = "https://www.kernel.org/releases.json"
ARCH_UPSTREAM_CONFIG_URL: Final = "https://gitlab.archlinux.org/archlinux/packaging/packages/linux/-/raw/main/config"
KERNEL_SIGNING_FPRS: Final = frozenset({
    "ABAF11C65A2970B130ABE3C479BE3E4300411886",  # Linus Torvalds
    "647F28654894E3BD457199BE38DBBDC86092693E",  # Greg Kroah-Hartman
    "E27E5D8A3403A2EF66873BBCDEA66FF797772CDC",  # Sasha Levin
})
USER_AGENT: Final = f"DuskyKernelCompiler/{APP_VERSION} (+Arch Linux)"

# ---------------------------------------------------------------------------------------------------
# Terminal UI primitives
# ---------------------------------------------------------------------------------------------------
ESC: Final = "\x1b"


class C:
    RESET = ESC + "[0m"
    BOLD = ESC + "[1m"
    DIM = ESC + "[2m"
    RED = ESC + "[31m"
    GREEN = ESC + "[32m"
    YELLOW = ESC + "[33m"
    BLUE = ESC + "[34m"
    MAGENTA = ESC + "[35m"
    CYAN = ESC + "[36m"
    ACCENT = ESC + "[38;5;141m"
    CLEAR_EOL = ESC + "[K"
    HIDE = ESC + "[?25l"
    SHOW = ESC + "[?25h"

    @classmethod
    def disable(cls) -> None:
        for name in ("RESET", "BOLD", "DIM", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "ACCENT", "CLEAR_EOL", "HIDE", "SHOW"):
            setattr(cls, name, "")


_ANSI_RE: Final = re.compile(ESC + r"\[[0-9;?]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def visible_len(s: str) -> int:
    return len(strip_ansi(s))


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - visible_len(s))


def term_width() -> int:
    try:
        return max(60, min(160, os.get_terminal_size().columns))
    except OSError:
        return 100


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


class Journal:
    """Plain-text build journal under ~/.local/state/dusky-kernel/logs."""

    def __init__(self) -> None:
        self.fh = None
        self.path: Path | None = None

    def open(self, name: str) -> None:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self.path = LOG_DIR / f"build-{name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.log"
            self.fh = open(self.path, "w", encoding="utf-8", buffering=1)
        except OSError:
            self.fh = None

    def write(self, s: str) -> None:
        if self.fh is not None:
            try:
                self.fh.write(strip_ansi(s) + "\n")
            except OSError:
                pass

    def close(self) -> None:
        if self.fh is not None:
            try:
                self.fh.close()
            finally:
                self.fh = None


JOURNAL: Final = Journal()
_VERBOSE: bool = False
ASSUME_YES: bool = False
_LIVE: "Live | None" = None
_OUT_LOCK: Final = threading.RLock()


def say(s: str = "") -> None:
    with _OUT_LOCK:
        if _LIVE is not None:
            _LIVE.emit(s)
        else:
            sys.stdout.write(s + "\n")
            sys.stdout.flush()
        JOURNAL.write(s)


def info(s: str) -> None:
    say(f"  {C.BLUE}::{C.RESET} {s}")


def ok(s: str) -> None:
    say(f"  {C.GREEN}✓{C.RESET} {s}")


def warn(s: str) -> None:
    say(f"  {C.YELLOW}▲{C.RESET} {s}")


def err(s: str) -> None:
    say(f"  {C.RED}✗{C.RESET} {s}")


def note(s: str) -> None:
    say(f"  {C.DIM}{s}{C.RESET}")


def debug(s: str) -> None:
    if _VERBOSE:
        say(f"  {C.DIM}debug: {s}{C.RESET}")


def rule(title: str = "") -> None:
    w = term_width()
    if not title:
        say(C.DIM + "─" * w + C.RESET)
        return
    t = f" {title.strip()} "
    fill = max(0, w - visible_len(t) - 4)
    say(f"{C.DIM}──{C.RESET}{C.BOLD}{t}{C.RESET}{C.DIM}{'─' * fill}{C.RESET}")


def banner() -> None:
    say(f"{C.CYAN}{C.BOLD}{APP_NAME} v{APP_VERSION}{C.RESET} {C.DIM}-- {APP_TAGLINE}{C.RESET}")


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    if not rows:
        return
    cells = [[str(v) for v in r] for r in rows]
    widths = [visible_len(h) for h in headers]
    for r in cells:
        for i, val in enumerate(r):
            widths[i] = max(widths[i], visible_len(val))
    say("  " + "  ".join(pad(C.BOLD + h + C.RESET, widths[i]) for i, h in enumerate(headers)))
    say("  " + "  ".join("─" * w for w in widths))
    for r in cells:
        say("  " + "  ".join(pad(val, widths[i]) for i, val in enumerate(r)))


def send_notification(title: str, msg: str, urgency: str = "normal", icon: str = "dialog-information") -> None:
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(["notify-send", "-a", "Dusky Kernel", "-u", urgency, "-i", icon, title, msg], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TiB"


# ---------------------------------------------------------------------------------------------------
# Prompts (KeyboardInterrupt intentionally propagates: Ctrl-C always aborts)
# ---------------------------------------------------------------------------------------------------
def ask(prompt: str, default: str = "") -> str:
    suffix = f" {C.DIM}[{default}]{C.RESET}" if default else ""
    try:
        return input(f"  {C.ACCENT}›{C.RESET} {prompt}{suffix}: ").strip() or default
    except EOFError:
        say("")
        return default


def ask_yes(prompt: str, default: bool = True) -> bool:
    if ASSUME_YES or not interactive():
        return default
    res = ask(f"{prompt} [{'Y/n' if default else 'y/N'}]").lower()
    if not res:
        return default
    return res in ("y", "yes", "true", "1", "on")


def ask_index(prompt: str, max_idx: int, default: int = 1) -> int:
    while True:
        val = ask(f"{prompt} (1-{max_idx})", str(default))
        if val.isdigit() and 1 <= int(val) <= max_idx:
            return int(val)
        warn(f"Enter an integer between 1 and {max_idx}")


def ask_choice(prompt: str, choices: Sequence[str], default: str) -> str:
    say(f"  {prompt} ({', '.join(choices)})")
    while True:
        val = ask("Choice", default)
        if val in choices:
            return val
        if val.isdigit() and 1 <= int(val) <= len(choices):
            return choices[int(val) - 1]
        warn(f"Invalid choice '{val}'. Pick from: {', '.join(choices)}")


def pause() -> None:
    if interactive() and not ASSUME_YES:
        ask("Press Enter to return", "")


# ---------------------------------------------------------------------------------------------------
# Error model (exit codes are part of the CLI contract)
# ---------------------------------------------------------------------------------------------------
class DuskyError(Exception):
    exit_code = 1


class ProfileError(DuskyError):
    exit_code = 2


class NetworkError(DuskyError):
    exit_code = 3


class VerifyError(DuskyError):
    exit_code = 4


class BuildError(DuskyError):
    exit_code = 5


class DependencyError(DuskyError):
    exit_code = 6


class AbortError(DuskyError):
    exit_code = 130


# ---------------------------------------------------------------------------------------------------
# Choice vocabularies (single source of truth for validation, CLI, wizard and Kconfig mapping)
# ---------------------------------------------------------------------------------------------------
CPU_ARCHES: Final = (
    "native", "generic", "generic_v2", "generic_v3", "generic_v4",
    "sandybridge", "ivybridge", "haswell", "broadwell", "skylake", "skylake-avx512", "icelake-client",
    "icelake-server", "tigerlake", "rocketlake", "alderlake", "raptorlake", "meteorlake", "arrowlake",
    "lunarlake", "sapphirerapids", "emeraldrapids", "graniterapids",
    "znver1", "znver2", "znver3", "znver4", "znver5",
)
# psABI level implied by a micro-architecture (what CONFIG_X86_64_VERSION can express) + graysky symbol.
UARCH_INFO: Final[dict[str, tuple[int, str]]] = {
    "generic": (1, "GENERIC_CPU"), "generic_v2": (2, "GENERIC_CPU2"), "generic_v3": (3, "GENERIC_CPU3"), "generic_v4": (4, "GENERIC_CPU4"),
    "sandybridge": (2, "MSANDYBRIDGE"), "ivybridge": (2, "MIVYBRIDGE"), "haswell": (3, "MHASWELL"), "broadwell": (3, "MBROADWELL"),
    "skylake": (3, "MSKYLAKE"), "skylake-avx512": (4, "MSKYLAKEX"), "icelake-client": (4, "MICELAKE_CLIENT"), "icelake-server": (4, "MICELAKE_SERVER"),
    "tigerlake": (4, "MTIGERLAKE"), "rocketlake": (4, "MROCKETLAKE"), "alderlake": (3, "MALDERLAKE"), "raptorlake": (3, "MRAPTORLAKE"),
    "meteorlake": (3, "MMETEORLAKE"), "arrowlake": (3, "MARROWLAKE"), "lunarlake": (3, "MLUNARLAKE"), "sapphirerapids": (4, "MSAPPHIRERAPIDS"),
    "emeraldrapids": (4, "MEMERALDRAPIDS"), "graniterapids": (4, "MGRANITERAPIDS"),
    "znver1": (3, "MZEN"), "znver2": (3, "MZEN2"), "znver3": (3, "MZEN3"), "znver4": (4, "MZEN4"), "znver5": (4, "MZEN5"),
}
GRAYSKY_SYMBOLS: Final = tuple(sorted({v[1] for v in UARCH_INFO.values()} | {"MNATIVE_INTEL", "MNATIVE_AMD", "MK8", "MPSC", "MCORE2", "MATOM"}))
HZ_CHOICES: Final = (100, 250, 300, 500, 600, 750, 1000)
HZ_UPSTREAM: Final = (100, 250, 300, 1000)
TICKLESS_CHOICES: Final = ("periodic", "idle", "full")
PREEMPT_CHOICES: Final = ("lazy", "full", "rt")
SCHED_CHOICES: Final = ("eevdf", "bore", "bmq")
SCX_CHOICES: Final = ("none", "scx_lavd", "scx_bpfland", "scx_layered", "scx_rusty", "scx_flash", "scx_p2dq", "scx_cosmos")
CHANNEL_CHOICES: Final = ("mainline", "stable", "longterm")
LTO_CHOICES: Final = ("none", "thin", "full")
OPT_CHOICES: Final = ("o2", "o3", "size")
FDO_CHOICES: Final = ("none", "autofdo", "autofdo_propeller")
THP_CHOICES: Final = ("always", "madvise", "never")
THP_DEFRAG_CHOICES: Final = ("always", "defer", "defer+madvise", "madvise", "never")
THP_SHMEM_CHOICES: Final = ("always", "within_size", "advise", "never")
SWAP_BACKEND_CHOICES: Final = ("zram", "zswap", "none")
ZRAM_ALGO_CHOICES: Final = ("zstd", "lz4", "lz4hc", "lzo-rle")
ZSWAP_COMP_CHOICES: Final = ("zstd", "lz4", "lz4hc", "lzo")
FOOTPRINT_CHOICES: Final = ("standard", "lean", "minimal", "embedded")
TRACING_CHOICES: Final = ("auto", "full", "minimal")
GOV_CHOICES: Final = ("schedutil", "performance", "powersave", "ondemand", "conservative")
EPP_CHOICES: Final = ("default", "performance", "balance_performance", "balance_power", "power")
PSTATE_CHOICES: Final = ("active", "guided", "passive", "disable", "undefined")
MITIGATION_CHOICES: Final = ("on", "off", "nosmt")
IDLE_GOV_CHOICES: Final = ("teo", "menu", "haltpoll")
PCIE_ASPM_CHOICES: Final = ("default", "powersave", "powersupersave", "performance")
CONG_CHOICES: Final = ("bbr", "cubic", "reno")
QDISC_CHOICES: Final = ("fq", "cake", "fq_codel", "fq_pie", "pfifo_fast")
TOOLCHAIN_CHOICES: Final = ("llvm", "gcc")
HEADERS_CHOICES: Final = ("auto", "always", "never")
MODULES_MODE_CHOICES: Final = ("strict", "expanded")
COMPRESS_CHOICES: Final = ("zstd", "xz", "gzip", "none")
DEBUG_CHOICES: Final = ("none", "reduced", "full")
SECURITY_PROFILES: Final = ("balanced", "extreme", "hardened")
STACKPROTECTOR_CHOICES: Final = ("strong", "regular", "none")
IOSCHED_CHOICES: Final = ("none", "mq-deadline", "bfq", "kyber", "keep")
SEED_CHOICES: Final = ("auto", "snapshot", "arch", "running", "headers", "defconfig")
CMDLINE_CHOICES: Final = ("bake", "entry", "print")

# Per-choice context shown by the wizard (section, key) -> {value: explanation}
CHOICE_HELP: Final[dict[tuple[str, str], dict[str, str]]] = {
    ("release", "channel"): {
        "mainline": "Linus' tree: newest features; 7.3-rc snapshots when allow_rc=true",
        "stable": "latest stable point release (7.2.y) -- recommended for daily drivers",
        "longterm": "LTS branch (still subject to the >= 7.2 floor)",
    },
    ("cpu", "arch"): {
        "native": "-march=native via CONFIG_X86_NATIVE_CPU: fastest, host-only, never portable",
        "generic": "x86-64 baseline (psABI v1): maximally portable",
        "generic_v2": "x86-64-v2: SSE4.2/POPCNT/CX16 (Nehalem+, Bulldozer+)",
        "generic_v3": "x86-64-v3: AVX2/BMI2/FMA/MOVBE (Haswell+, Zen+) -- best portable choice",
        "generic_v4": "x86-64-v4: AVX-512 -- the kernel never emits AVX-512, so this equals v3 for kernel code",
    },
    ("cpu", "governor"): {
        "schedutil": "utilization-driven, integrates with EEVDF/sched_ext (default)",
        "performance": "pin maximum frequency (desktops on wall power)",
        "powersave": "with intel_pstate/amd_pstate=active this is the HWP-driven default and is not slow",
        "ondemand": "legacy sampling governor (only meaningful with acpi-cpufreq)",
        "conservative": "legacy sampling governor with slow ramp (acpi-cpufreq only)",
    },
    ("cpu", "amd_pstate"): {
        "active": "EPP autonomous mode: firmware picks frequency, kernel provides hints (Zen 2+ best)",
        "guided": "kernel provides min/max band, firmware selects inside it",
        "passive": "kernel-driven target frequency (CPPC, non-autonomous)",
        "disable": "fall back to acpi-cpufreq",
        "undefined": "leave the Kconfig default (3 = active)",
    },
    ("cpu", "epp"): {
        "default": "do not touch the firmware EPP hint",
        "performance": "EPP 0: maximum performance bias",
        "balance_performance": "EPP 128: desktop default bias",
        "balance_power": "EPP 192: efficiency bias",
        "power": "EPP 255: maximum efficiency bias (battery)",
    },
    ("cpu", "mitigations"): {
        "on": "build and enable CPU vulnerability mitigations (safe default)",
        "off": "compile out CONFIG_CPU_MITIGATIONS and boot with mitigations=off (faster syscalls/IO; trusted single-user machines only)",
        "nosmt": "keep mitigations, boot with mitigations=auto,nosmt (disables SMT siblings)",
    },
    ("scheduler", "type"): {
        "eevdf": "upstream EEVDF (7.x default) -- required for sched_ext BPF classes",
        "bore": "BORE: burst-oriented EEVDF variant (out-of-tree patch, sched_ext compatible)",
        "bmq": "Project C BMQ (out-of-tree patch replacing EEVDF; incompatible with sched_ext)",
    },
    ("scheduler", "scx"): {
        "none": "no BPF scheduler daemon (sched_ext class may still be compiled)",
        "scx_lavd": "latency-criticality aware (gaming/handheld); flags: --autopilot or --autopower",
        "scx_bpfland": "interactive-first vruntime scheduler; '-m performance' for gaming",
        "scx_layered": "config-driven layered scheduler (workstations, mixed workloads)",
        "scx_rusty": "multi-domain hybrid user/BPF (servers, large NUMA)",
        "scx_flash": "EDF-style fairness with predictable latency (audio, soft-RT)",
        "scx_p2dq": "pick-2 dispatch queues, LLC-aware, simple and robust",
        "scx_cosmos": "lightweight LLC/hybrid-topology aware scheduler",
    },
    ("timing", "tickless"): {
        "periodic": "always tick (legacy; only for debugging)",
        "idle": "NO_HZ_IDLE: stop ticks on idle CPUs (desktop/laptop default)",
        "full": "NO_HZ_FULL: adaptive ticks -- only useful with nohz_full=/isolcpus= CPU isolation (RT/HPC)",
    },
    ("timing", "preempt"): {
        "lazy": "PREEMPT_LAZY: full-preempt responsiveness with voluntary-like throughput (7.x desktop default)",
        "full": "PREEMPT: lowest scheduling latency",
        "rt": "PREEMPT_RT: hard real-time; disables PREEMPT_DYNAMIC and costs throughput",
    },
    ("memory", "thp"): {
        "always": "THP for all anonymous memory (fastest for games/JVMs; more RSS, khugepaged activity)",
        "madvise": "THP only where applications ask (balanced default)",
        "never": "no THP: smallest footprint, no khugepaged (low-RAM)",
    },
    ("memory", "swap_backend"): {
        "zram": "compressed RAM swap via zram-generator: best for systems without disk swap",
        "zswap": "compressed cache in front of an existing disk swap device",
        "none": "no compressed swap layer (zswap disabled)",
    },
    ("memory", "zram_algo"): {
        "zstd": "best ratio (~3-4x), higher CPU cost",
        "lz4": "fastest, lower ratio (~2-2.5x); pair with zstd recompression",
        "lz4hc": "lz4 high-compression variant (slow compress, fast decompress)",
        "lzo-rle": "kernel default; balanced",
    },
    ("memory", "footprint"): {
        "standard": "distribution-like feature set",
        "lean": "<= 8 GiB: drop debug/tracing/legacy cgroup v1/kexec/kcore, smaller log buffer",
        "minimal": "<= 4-8 GiB, sub-300 MiB idle target: lean + SLUB_TINY, no hugetlbfs, no KALLSYMS_ALL, DAMON reclaim",
        "embedded": "<= 4 GiB headless/appliance: minimal + BASE_SMALL, no 32-bit compat, no hibernation",
    },
    ("memory", "tracing"): {
        "auto": "full tracing when a sched_ext daemon is selected, minimal otherwise",
        "full": "ftrace + kprobes + uprobes + BPF events (perf, bpftrace, scx tooling)",
        "minimal": "tracepoints + BPF syscall only; no function tracer/kprobes (smaller text, fewer pages)",
    },
    ("compiler", "toolchain"): {
        "llvm": "clang/lld: LTO, kCFI, AutoFDO/Propeller, ThinLTO cache",
        "gcc": "GCC + ld.bfd: no LTO/kCFI/FDO paths",
    },
    ("compiler", "optimize"): {
        "o2": "-O2: upstream default, best tested",
        "o3": "-O3: aggressive instruction scheduling, loop pipelining, and x86 SIMD safety guards",
        "size": "-Os: ~15-25% smaller text; slower hot paths; for minimal/embedded footprints",
    },
    ("compiler", "lto"): {
        "none": "no link-time optimization (fastest builds, compatible with Rust+BTF)",
        "thin": "ThinLTO: parallel, cached in ~/.cache/dusky-kernel/thinlto-cache",
        "full": "monolithic LTO: best codegen; vmlinux link needs ~16-24 GiB RAM and is single-threaded",
    },
    ("compiler", "fdo"): {
        "none": "no profile-guided optimization",
        "autofdo": "CONFIG_AUTOFDO_CLANG with a perf-derived .afdo profile",
        "autofdo_propeller": "AutoFDO + Propeller basic-block layout (needs create_llvm_prof profiles)",
    },
    ("compiler", "debug_info"): {
        "none": "DEBUG_INFO_NONE (forced to DWARF5 when BTF is required by sched_ext/BPF)",
        "reduced": "DWARF5 debug info (needed for BTF); no runtime memory cost",
        "full": "full DWARF5, uncompressed (largest build tree)",
    },
    ("security", "profile"): {
        "balanced": "Arch-like hardening: usercopy checks, init_on_alloc, freelist hardening, UBSAN bounds",
        "extreme": "performance over hardening: disables most runtime checks (requires acknowledge_risk)",
        "hardened": "KSPP-style: adds init_on_free, kCFI, random kmalloc caches, strict IOMMU, lockdown early",
    },
    ("storage", "io_scheduler"): {
        "none": "no scheduler for NVMe (lowest overhead)",
        "mq-deadline": "deadline-based, good for SATA SSDs",
        "bfq": "fairness/latency oriented, best for rotational disks",
        "kyber": "token-based low-latency scheduler for fast SSDs",
        "keep": "do not install I/O scheduler udev rules",
    },
    ("power", "cpu_idle_governor"): {
        "teo": "timer-events oriented: best for tickless desktops/laptops",
        "menu": "classic predictive governor",
        "haltpoll": "for KVM guests (polls before halting)",
    },
    ("network", "congestion"): {"bbr": "model-based, best for internet links", "cubic": "loss-based upstream default", "reno": "classic"},
    ("network", "qdisc"): {
        "fq": "fair queue pacing (pairs with BBR)", "cake": "bufferbloat killer for home links (runtime sysctl; Kconfig falls back to fq_codel)",
        "fq_codel": "upstream default", "fq_pie": "PIE-based AQM", "pfifo_fast": "legacy FIFO",
    },
    ("modules", "mode"): {
        "strict": "hardware-only via modprobed.db (LSMOD): tiny module set, tiny memory",
        "expanded": "modprobed.db + LMC_KEEP safety net (USB/GPU/net/HID/fs stay available)",
    },
    ("compiler", "headers"): {
        "auto": "build -headers only when DKMS modules are installed (nvidia, zfs, v4l2loopback...)",
        "always": "always build the -headers package",
        "never": "never build headers (enables TRIM_UNUSED_KSYMS eligibility)",
    },
    ("dusky", "seed"): {
        "auto": "snapshot -> Arch upstream config -> /proc/config.gz -> headers -> defconfig",
        "snapshot": "only the saved snapshot for this profile",
        "arch": "Arch Linux packaging config (gitlab.archlinux.org)",
        "running": "/proc/config.gz of the running kernel",
        "headers": "/usr/lib/modules/$(uname -r)/build/.config",
        "defconfig": "make defconfig (last resort; not desktop-complete)",
    },
    ("boot", "cmdline"): {
        "bake": "compile flavor tuning into CONFIG_CMDLINE (bootloader options still win; per-kernel)",
        "entry": "write tuning into the systemd-boot entry for this flavor only",
        "print": "only print the recommended command line",
    },
}


# ---------------------------------------------------------------------------------------------------
# Profile schema
# ---------------------------------------------------------------------------------------------------
type FieldKind = Literal["str", "int", "bool", "list", "table"]


@dataclass(frozen=True, slots=True)
class FieldSpec:
    key: str
    kind: FieldKind
    default: Any
    help: str
    choices: tuple[Any, ...] | None = None
    required: bool = False
    wizard: bool = True
    minimum: int | None = None
    maximum: int | None = None


def F(key: str, kind: FieldKind, default: Any, help: str, choices: Iterable[Any] | None = None, *,
      required: bool = False, wizard: bool = True, minimum: int | None = None, maximum: int | None = None) -> FieldSpec:
    return FieldSpec(key, kind, default, help, tuple(choices) if choices else None, required, wizard, minimum, maximum)


PROFILE_SPEC: Final[dict[str, tuple[FieldSpec, ...]]] = {
    "meta": (
        F("name", "str", "", "Profile id (matches --profile)", required=True, wizard=False),
        F("description", "str", "", "One-line summary", wizard=False),
        F("suffix", "str", "", "LOCALVERSION suffix and pkgbase tail: linux-<suffix>", required=True, wizard=False),
        F("priority", "int", 50, "Sort order in pickers", wizard=False),
        F("tags", "list", [], "Free-form labels", wizard=False),
        F("bare_metal_only", "bool", False, "Refuse to build inside a VM and strip guest paravirt code"),
        F("portable_package", "bool", False, "Package targets another machine (forbids -march=native)"),
    ),
    "release": (
        F("channel", "str", "stable", "Upstream release channel", CHANNEL_CHOICES),
        F("pin", "str", "", "Exact version pin (e.g. 7.2.3 or 7.3-rc2); empty = newest in channel"),
        F("allow_rc", "bool", True, "Allow -rc snapshot tarballs (mainline)"),
        F("min_version", "str", "7.2", "Hard floor; anything older is rejected", wizard=False),
        F("require_signature", "bool", True, "Require PGP or SHA256 verification of release tarballs"),
    ),
    "scheduler": (
        F("type", "str", "eevdf", "Base scheduler", SCHED_CHOICES),
        F("scx", "str", "none", "sched_ext BPF scheduler daemon", SCX_CHOICES),
        F("scx_flags", "str", "", "Flags passed to the scx daemon (e.g. --autopilot)"),
        F("scx_enable_class", "bool", True, "Compile CONFIG_SCHED_CLASS_EXT (+BTF/BPF JIT)"),
        F("require_patch", "bool", False, "Fail the build if the scheduler patch cannot be applied"),
        F("allow_vanilla_fallback", "bool", True, "Fall back to EEVDF when the patch fails"),
        F("autogroup", "bool", True, "SCHED_AUTOGROUP (per-session fairness)"),
        F("rt_group", "bool", False, "RT_GROUP_SCHED bandwidth control"),
        F("sched_core", "bool", False, "SCHED_CORE core scheduling (SMT side-channel isolation; overhead)"),
        F("patch_sources", "list", ["cachyos", "upstream_author", "tkg"], "Ordered patch resolvers", wizard=False),
    ),
    "cache": (
        F("sched_cache", "bool", True, "CONFIG_SCHED_CACHE Cache-Aware Scheduling (LLC affinity)"),
        F("llc_aggr_tolerance", "int", 1, "LLC aggregation tolerance written to debugfs at boot", minimum=0, maximum=100),
        F("llc_aggr_cap", "int", -1, "LLC aggregation capacity percent (-1 = kernel default)", minimum=-1, maximum=100),
    ),
    "rseq": (
        F("slice_extension", "bool", True, "RSEQ time-slice extension (CONFIG_RSEQ_SLICE_EXTENSION)"),
        F("slice_ext_nsec", "int", 10000, "Requested slice extension in nanoseconds", minimum=1000, maximum=100000),
    ),
    "dusky": (
        F("enhanced", "bool", False, "Desktop heuristics (nowatchdog, faster fbcon takeover)"),
        F("patch_sched_inline", "bool", True, "Inline finish_task_switch() (~8.6%-35% context switch speedup)"),
        F("patch_evdev_rcu", "bool", True, "Asynchronous evdev detach (eliminates 27s input close stalls)"),
        F("patch_pci_pme", "bool", True, "Clear Linux 4000ms PCI PME polling timeout (reduces wakeups/saves power)"),
        F("hostname", "str", "", "KBUILD_BUILD_HOST (empty = system hostname)", wizard=False),
        F("user", "str", "", "KBUILD_BUILD_USER (empty = dynamic active user)", wizard=False),
        F("reproducible", "bool", True, "Fixed KBUILD_BUILD_TIMESTAMP / SOURCE_DATE_EPOCH", wizard=False),
        F("seed", "str", "auto", "Seed .config source", SEED_CHOICES),
        F("extra_config", "table", {}, "Arbitrary Kconfig overrides: SYMBOL = true|false|\"m\"|int|\"string\""),
    ),
    "cpu": (
        F("arch", "str", "native", "Target micro-architecture", CPU_ARCHES),
        F("march", "str", "", "Extra -march/-mtune override appended to KCFLAGS", wizard=False),
        F("governor", "str", "schedutil", "Default cpufreq governor", GOV_CHOICES),
        F("amd_pstate", "str", "active", "AMD P-State operation mode", PSTATE_CHOICES),
        F("epp", "str", "balance_performance", "Energy Performance Preference hint applied at boot", EPP_CHOICES),
        F("mitigations", "str", "on", "Speculative-execution mitigations", MITIGATION_CHOICES),
        F("nr_cpus", "int", 0, "CONFIG_NR_CPUS (0 = host thread count rounded up to 8)", minimum=0, maximum=8192),
        F("smt", "bool", True, "Keep SMT/Hyper-Threading enabled"),
        F("mce", "bool", True, "Machine Check Exception handling"),
        F("prefcore", "bool", True, "AMD preferred-core / Intel ITMT priority (SCHED_MC_PRIO)"),
        F("compat32", "bool", True, "IA32_EMULATION (Steam, Wine, 32-bit binaries)"),
    ),
    "timing": (
        F("hz", "int", 1000, "Timer tick frequency", HZ_CHOICES),
        F("tickless", "str", "idle", "Tickless mode", TICKLESS_CHOICES),
        F("preempt", "str", "lazy", "Preemption model", PREEMPT_CHOICES),
        F("preempt_dynamic", "bool", True, "PREEMPT_DYNAMIC (preempt= boot switch)"),
    ),
    "memory": (
        F("footprint", "str", "standard", "Memory footprint tier (bundles many small Kconfig cuts)", FOOTPRINT_CHOICES),
        F("thp", "str", "madvise", "Transparent Hugepages mode", THP_CHOICES),
        F("thp_defrag", "str", "defer+madvise", "THP defrag strategy", THP_DEFRAG_CHOICES),
        F("thp_shmem", "str", "never", "THP for shmem/tmpfs", THP_SHMEM_CHOICES),
        F("mglru", "bool", True, "Multi-Gen LRU"),
        F("mglru_mask", "int", 7, "lru_gen/enabled bitmask", minimum=0, maximum=7),
        F("mglru_min_ttl_ms", "int", 1000, "lru_gen/min_ttl_ms anti-thrash threshold", minimum=0, maximum=60000),
        F("swap_backend", "str", "zram", "Compressed swap backend", SWAP_BACKEND_CHOICES),
        F("zram_algo", "str", "zstd", "Primary ZRAM compressor", ZRAM_ALGO_CHOICES),
        F("zram_recomp_algo", "str", "zstd", "ZRAM recompression algorithm for idle pages (multi-comp)", ZRAM_ALGO_CHOICES),
        F("zram_size_pct", "int", 100, "ZRAM size as percent of RAM", minimum=10, maximum=400),
        F("zram_multi_comp", "bool", True, "CONFIG_ZRAM_MULTI_COMP (multi-algorithm swap compression in kernel)"),
        F("zswap_compressor", "str", "zstd", "zswap compressor", ZSWAP_COMP_CHOICES),
        F("zswap_max_pool_pct", "int", 25, "zswap.max_pool_percent", minimum=5, maximum=80),
        F("swappiness", "int", 0, "vm.swappiness (0 = auto: 180 zram, 100 zswap, 60 none)", minimum=0, maximum=200),
        F("vfs_cache_pressure", "int", 0, "vm.vfs_cache_pressure (0 = auto by footprint)", minimum=0, maximum=1000),
        F("watermark_scale_factor", "int", 125, "vm.watermark_scale_factor", minimum=10, maximum=3000),
        F("watermark_boost_factor", "int", 0, "vm.watermark_boost_factor (0 recommended with zram)", minimum=0, maximum=30000),
        F("compaction_proactiveness", "int", 0, "vm.compaction_proactiveness (0 = auto: 20 with THP, 0 without)", minimum=0, maximum=100),
        F("dirty_bytes_mb", "int", 0, "vm.dirty_bytes in MiB (0 = kernel ratio defaults)", minimum=0, maximum=65536),
        F("slub_tiny", "bool", False, "CONFIG_SLUB_TINY minimal allocator (sacrifices SMP scalability)"),
        F("slab_buckets", "bool", False, "CONFIG_SLAB_BUCKETS hardening buckets (slightly more memory)"),
        F("per_vma_lock", "bool", True, "CONFIG_PER_VMA_LOCK"),
        F("numa", "bool", True, "NUMA topology support"),
        F("numa_balancing", "bool", False, "Automatic NUMA balancing"),
        F("nodes_shift", "int", 2, "CONFIG_NODES_SHIFT", minimum=0, maximum=10),
        F("ksm", "bool", True, "Kernel Samepage Merging support"),
        F("ksm_run", "bool", False, "Activate ksmd at boot (only merges MADV_MERGEABLE/prctl opted-in memory)"),
        F("damon", "bool", False, "DAMON monitoring + DAMON_RECLAIM proactive reclaim"),
        F("page_reporting", "bool", False, "Free page reporting to the hypervisor (VM guests)"),
        F("hugetlbfs", "bool", True, "hugetlbfs / HugeTLB pages"),
        F("kallsyms_all", "bool", True, "KALLSYMS_ALL (all symbols incl. data; ~1-2 MiB)"),
        F("memcg", "bool", True, "cgroup v2 memory controller (systemd MemoryMax, oomd)"),
        F("base_small", "bool", False, "BASE_SMALL: shrink core hash tables (embedded)"),
        F("log_buf_shift", "int", 0, "CONFIG_LOG_BUF_SHIFT (0 = 17 standard / 16 lean / 15 minimal)", minimum=0, maximum=25),
        F("tracing", "str", "auto", "ftrace/kprobes/uprobes surface", TRACING_CHOICES),
        F("kexec", "bool", True, "kexec + crash dump support"),
        F("ikconfig", "bool", True, "Embed .config (/proc/config.gz)"),
        F("trim_unused_ksyms", "bool", False, "TRIM_UNUSED_KSYMS (breaks out-of-tree modules; only with headers=never)"),
        F("dead_code_elimination", "bool", False, "LD_DEAD_CODE_DATA_ELIMINATION (inert on upstream x86-64)"),
    ),
    "compiler": (
        F("toolchain", "str", "llvm", "Toolchain", TOOLCHAIN_CHOICES),
        F("optimize", "str", "o2", "Optimization level", OPT_CHOICES),
        F("polly", "bool", False, "Clang Polly loop optimizer (CONFIG_POLLY_CLANG)"),
        F("lto", "str", "thin", "Link-time optimization", LTO_CHOICES),
        F("thinlto_cache", "bool", True, "Persist the ThinLTO cache across builds"),
        F("thinlto_cache_size_gb", "int", 20, "Prune the ThinLTO cache above this size", minimum=1, maximum=500),
        F("fdo", "str", "none", "Feedback-directed optimization", FDO_CHOICES),
        F("fdo_profile_dir", "str", "", "Directory holding kernel.afdo / propeller_* profiles"),
        F("kcfi", "bool", False, "Clang kCFI (CONFIG_CFI) + FineIBT auto"),
        F("debug_info", "str", "reduced", "Debug info level", DEBUG_CHOICES),
        F("module_compress", "str", "zstd", "Module compression", COMPRESS_CHOICES),
        F("rust", "bool", True, "Rust support (auto-disabled when LTO + BTF are both required)"),
        F("jobs", "int", 0, "Parallel jobs (0 = auto from CPU threads and RAM)", minimum=0, maximum=1024),
        F("headers", "str", "auto", "Headers package policy", HEADERS_CHOICES),
        F("modversions", "bool", False, "MODVERSIONS symbol CRCs (needs GENDWARFKSYMS with Rust)"),
    ),
    "security": (
        F("profile", "str", "balanced", "Hardening bundle", SECURITY_PROFILES),
        F("init_on_alloc", "bool", True, "Zero memory on allocation"),
        F("init_on_free", "bool", False, "Zero memory on free (expensive)"),
        F("hardened_usercopy", "bool", True, "Hardened usercopy bounds checks"),
        F("stackprotector", "str", "strong", "Stack protector", STACKPROTECTOR_CHOICES),
        F("slab_freelist_hardened", "bool", True, "SLAB freelist pointer obfuscation"),
        F("slab_freelist_random", "bool", True, "Randomized freelists"),
        F("randomize_kstack", "bool", True, "Randomize kernel stack offset per syscall"),
        F("ubsan_bounds", "bool", True, "UBSAN array-bounds instrumentation"),
        F("apparmor", "bool", False, "Build AppArmor and place it in the default LSM order"),
        F("selinux", "bool", False, "Build SELinux (kept out of the default LSM order)"),
        F("lockdown_early", "bool", False, "SECURITY_LOCKDOWN_LSM_EARLY"),
        F("acknowledge_risk", "bool", False, "Acknowledge extreme profile / mitigations=off risks"),
    ),
    "gaming": (
        F("ntsync", "bool", True, "In-tree NTSync driver (CONFIG_NTSYNC=m)"),
        F("uclamp", "bool", True, "UCLAMP_TASK utilization clamping"),
        F("max_map_count", "int", 2147483642, "vm.max_map_count", minimum=65530, maximum=2147483642),
        F("split_lock_mitigate", "bool", False, "Split-lock detection penalty (off = better emulator/game frametimes)"),
        F("controllers", "bool", True, "Keep controller/HID drivers (xpad, playstation, nintendo, steam, uinput)"),
    ),
    "storage": (
        F("nvme_poll_queues", "int", 0, "nvme.poll_queues (IOPOLL) count", minimum=0, maximum=128),
        F("io_scheduler", "str", "none", "NVMe I/O scheduler udev default", IOSCHED_CHOICES),
        F("blk_wbt", "bool", True, "Block writeback throttling"),
        F("iocost", "bool", False, "BLK_CGROUP_IOCOST proportional I/O control"),
        F("extra_filesystems", "list", [], "Additional filesystems to keep as modules (e.g. xfs, f2fs)"),
    ),
    "power": (
        F("wq_power_efficient", "bool", False, "Power-efficient unbound workqueues"),
        F("cpu_idle_governor", "str", "teo", "cpuidle governor", IDLE_GOV_CHOICES),
        F("rcu_lazy", "bool", False, "RCU lazy callbacks on all CPUs (battery)"),
        F("energy_model", "bool", False, "Energy model / EAS"),
        F("suspend", "bool", True, "Suspend-to-idle/RAM"),
        F("hibernation", "bool", True, "Hibernation (zstd compressed image)"),
        F("pcie_aspm", "str", "default", "Default PCIe ASPM policy", PCIE_ASPM_CHOICES),
        F("hda_power_save", "int", 0, "SND_HDA_POWER_SAVE_DEFAULT seconds (0 = off)", minimum=0, maximum=3600),
    ),
    "network": (
        F("congestion", "str", "bbr", "TCP congestion control", CONG_CHOICES),
        F("qdisc", "str", "fq", "Root queueing discipline", QDISC_CHOICES),
        F("mptcp", "bool", True, "Multipath TCP"),
        F("xdp", "bool", False, "AF_XDP sockets"),
        F("nf_conntrack_procfs", "bool", False, "Legacy /proc/net/nf_conntrack"),
        F("tcp_fastopen", "bool", True, "net.ipv4.tcp_fastopen=3"),
    ),
    "modules": (
        F("mode", "str", "strict", "Pruning mode", MODULES_MODE_CHOICES),
        F("modprobed_db", "bool", True, "Use modprobed.db as LSMOD"),
        F("modprobed_db_path", "str", "", "Custom modprobed.db path (imported bundles)"),
        F("allow_lsmod_fallback", "bool", False, "Strict mode may fall back to the live lsmod set"),
        F("lmc_keep_extra", "list", [], "Extra LMC_KEEP paths (expanded mode)"),
        F("keep_symbols", "list", [], "Kconfig symbols forced to =m after pruning (e.g. WIREGUARD, TUN)"),
        F("localyesconfig", "bool", False, "Build pruned modules into the image (localyesconfig)"),
        F("sig_force", "bool", False, "MODULE_SIG_FORCE (auto-generated key)"),
    ),
    "boot": (
        F("cmdline", "str", "bake", "How flavor tuning reaches the kernel command line", CMDLINE_CHOICES),
        F("cmdline_extra", "str", "", "Extra kernel parameters appended to the flavor tuning"),
        F("write_entries", "bool", True, "Write/refresh systemd-boot entries for this flavor"),
        F("nowatchdog", "bool", True, "Disable NMI/soft watchdog (nowatchdog nmi_watchdog=0)"),
        F("acs_override", "bool", False, "PCIe ACS override for broken IOMMU groups (VFIO GPU passthrough; dangerous)"),
    ),
    "verify": (
        F("strict", "bool", True, "Hard-fail when a non-optional Kconfig contract entry is unmet"),
        F("optional_symbols", "list", [], "Extra symbols treated as soft in the contract", wizard=False),
        F("require_ntsync", "bool", True, "Require NTSync when gaming.ntsync=true"),
        F("require_btf", "bool", True, "Require BTF whenever sched_ext is compiled"),
        F("require_sched_ext", "bool", True, "Require SCHED_CLASS_EXT when scx daemon != none"),
    ),
}


@dataclass(frozen=True, slots=True)
class WizardStep:
    title: str
    groups: tuple[tuple[str, tuple[str, ...]], ...]


WIZARD_STEPS: Final[tuple[WizardStep, ...]] = (
    WizardStep("Release", (("release", ("channel", "pin", "allow_rc", "require_signature")),)),
    WizardStep("CPU", (("cpu", ("arch", "governor", "amd_pstate", "epp", "mitigations", "nr_cpus", "smt", "prefcore", "compat32", "mce")),)),
    WizardStep("Scheduler", (("scheduler", ("type", "scx", "scx_flags", "scx_enable_class", "require_patch", "allow_vanilla_fallback", "autogroup", "rt_group", "sched_core")),
                             ("cache", ("sched_cache", "llc_aggr_tolerance", "llc_aggr_cap")),
                             ("rseq", ("slice_extension", "slice_ext_nsec")))),
    WizardStep("Timing", (("timing", ("hz", "tickless", "preempt", "preempt_dynamic")),)),
    WizardStep("Memory & Low-RAM", (("memory", ("footprint", "thp", "thp_defrag", "thp_shmem", "mglru", "mglru_mask", "mglru_min_ttl_ms", "swap_backend",
                                                "zram_algo", "zram_recomp_algo", "zram_size_pct", "zram_multi_comp", "zswap_compressor", "zswap_max_pool_pct",
                                                "swappiness", "vfs_cache_pressure", "watermark_scale_factor", "watermark_boost_factor", "compaction_proactiveness",
                                                "dirty_bytes_mb", "slub_tiny", "slab_buckets", "per_vma_lock", "numa", "numa_balancing", "nodes_shift", "ksm", "ksm_run",
                                                "damon", "page_reporting", "hugetlbfs", "kallsyms_all", "memcg", "base_small", "log_buf_shift", "tracing", "kexec",
                                                "ikconfig", "trim_unused_ksyms", "dead_code_elimination")),)),
    WizardStep("Compiler & Toolchain", (("compiler", ("toolchain", "optimize", "polly", "lto", "thinlto_cache", "thinlto_cache_size_gb", "kcfi", "fdo", "fdo_profile_dir",
                                                       "debug_info", "module_compress", "rust", "jobs", "modversions")),
                                        ("dusky", ("seed", "enhanced", "patch_sched_inline", "patch_evdev_rcu", "patch_pci_pme", "extra_config")))),
    WizardStep("Security", (("security", ("profile", "init_on_alloc", "init_on_free", "hardened_usercopy", "stackprotector", "slab_freelist_hardened",
                                          "slab_freelist_random", "randomize_kstack", "ubsan_bounds", "apparmor", "selinux", "lockdown_early", "acknowledge_risk")),)),
    WizardStep("Gaming / Low-Latency", (("gaming", ("ntsync", "uclamp", "max_map_count", "split_lock_mitigate", "controllers")),)),
    WizardStep("Storage & Power", (("storage", ("nvme_poll_queues", "io_scheduler", "blk_wbt", "iocost", "extra_filesystems")),
                                   ("power", ("cpu_idle_governor", "rcu_lazy", "energy_model", "wq_power_efficient", "suspend", "hibernation", "pcie_aspm", "hda_power_save")))),
    WizardStep("Network", (("network", ("congestion", "qdisc", "mptcp", "xdp", "nf_conntrack_procfs", "tcp_fastopen")),)),
    WizardStep("Modules, Headers & Boot", (("modules", ("mode", "modprobed_db", "modprobed_db_path", "allow_lsmod_fallback", "lmc_keep_extra", "keep_symbols",
                                                        "localyesconfig", "sig_force")),
                                           ("compiler", ("headers",)),
                                           ("boot", ("cmdline", "cmdline_extra", "write_entries", "nowatchdog", "acs_override")),
                                           ("meta", ("bare_metal_only", "portable_package")),
                                           ("verify", ("strict", "require_ntsync", "require_btf", "require_sched_ext")))),
)

# ---------------------------------------------------------------------------------------------------
# Profile model
# ---------------------------------------------------------------------------------------------------
SECURITY_BUNDLES: Final[dict[str, dict[str, Any]]] = {
    "balanced": {"init_on_alloc": True, "init_on_free": False, "hardened_usercopy": True, "stackprotector": "strong",
                 "slab_freelist_hardened": True, "slab_freelist_random": True, "randomize_kstack": True, "ubsan_bounds": True, "lockdown_early": False},
    "extreme": {"init_on_alloc": False, "init_on_free": False, "hardened_usercopy": False, "stackprotector": "regular",
                "slab_freelist_hardened": False, "slab_freelist_random": False, "randomize_kstack": False, "ubsan_bounds": False, "lockdown_early": False},
    "hardened": {"init_on_alloc": True, "init_on_free": True, "hardened_usercopy": True, "stackprotector": "strong",
                 "slab_freelist_hardened": True, "slab_freelist_random": True, "randomize_kstack": True, "ubsan_bounds": True, "lockdown_early": True},
}
FOOTPRINT_RANK: Final = {name: i for i, name in enumerate(FOOTPRINT_CHOICES)}


@dataclass(slots=True)
class KernelProfile:
    path: Path
    sections: Sections
    explicit: set[tuple[str, str]] = field(default_factory=set)
    notices: list[str] = field(default_factory=list)

    def g(self, section: str, key: str, default: Any = None) -> Any:
        return self.sections.get(section, {}).get(key, default)

    def set(self, section: str, key: str, value: Any, *, explicit: bool = True) -> None:
        self.sections.setdefault(section, {})[key] = value
        if explicit:
            self.explicit.add((section, key))

    @property
    def name(self) -> str:
        return str(self.g("meta", "name") or self.path.stem)

    @property
    def description(self) -> str:
        return str(self.g("meta", "description", ""))

    @property
    def priority(self) -> int:
        return int(self.g("meta", "priority", 50))

    @property
    def suffix(self) -> str:
        return str(self.g("meta", "suffix") or self.name).strip().strip("-")

    def localversion(self) -> str:
        return "-" + self.suffix

    @property
    def pkgbase(self) -> str:
        return "linux-" + self.suffix

    @property
    def footprint_rank(self) -> int:
        return FOOTPRINT_RANK[self.g("memory", "footprint")]

    def lean(self, tier: str) -> bool:
        return self.footprint_rank >= FOOTPRINT_RANK[tier]

    def clone(self) -> Self:
        return type(self)(self.path, json.loads(json.dumps(self.sections)), set(self.explicit), list(self.notices))

    def summarize(self) -> list[tuple[str, str]]:
        s = self.sections
        sched = s["scheduler"]["type"] + (f" + {s['scheduler']['scx']}" if s["scheduler"]["scx"] != "none" else "")
        return [
            ("profile", f"{self.name}  ({self.pkgbase})"), ("channel", s["release"]["channel"] + (f" pin={s['release']['pin']}" if s["release"]["pin"] else "")),
            ("cpu", f"{s['cpu']['arch']} | gov={s['cpu']['governor']} | amd_pstate={s['cpu']['amd_pstate']} | mitigations={s['cpu']['mitigations']}"),
            ("scheduler", f"{sched} | CAS={'on' if s['cache']['sched_cache'] else 'off'}"),
            ("timing", f"{s['timing']['hz']}Hz | {s['timing']['tickless']} | preempt={s['timing']['preempt']}{' (dynamic)' if s['timing']['preempt_dynamic'] else ''}"),
            ("memory", f"footprint={s['memory']['footprint']} | THP={s['memory']['thp']} | swap={s['memory']['swap_backend']} | MGLRU={'on' if s['memory']['mglru'] else 'off'}"),
            ("toolchain", f"{s['compiler']['toolchain']} | {s['compiler']['optimize']} | lto={s['compiler']['lto']} | kcfi={'on' if s['compiler']['kcfi'] else 'off'} | rust={'on' if s['compiler']['rust'] else 'off'}"),
            ("security", f"{s['security']['profile']}"),
            ("modules", f"{s['modules']['mode']} | headers={s['compiler']['headers']}"),
            ("network", f"{s['network']['congestion']} / {s['network']['qdisc']}"),
        ]


def _coerce_value(spec: FieldSpec, val: Any) -> Any:
    match spec.kind:
        case "bool":
            if isinstance(val, bool):
                return val
            return str(val).strip().lower() in ("true", "1", "yes", "on", "y")
        case "int":
            if isinstance(val, bool):
                return int(val)
            if isinstance(val, int):
                return val
            try:
                return int(str(val).strip())
            except ValueError:
                return spec.default
        case "list":
            if isinstance(val, str):
                return [x.strip() for x in val.split(",") if x.strip()]
            return [str(x) for x in (val or [])]
        case "table":
            return dict(val) if isinstance(val, Mapping) else {}
        case _:
            return "" if val is None else str(val)


def coerce(data: Mapping[str, Any], path: Path) -> tuple[Sections, set[tuple[str, str]]]:
    res: Sections = {}
    explicit: set[tuple[str, str]] = set()
    for sec in data:
        if sec not in PROFILE_SPEC:
            warn(f"{path.name}: unknown section [{sec}] ignored")
    for sec, fields in PROFILE_SPEC.items():
        in_sec = data.get(sec, {}) or {}
        known = {f.key for f in fields}
        for key in in_sec:
            if key not in known:
                warn(f"{path.name}: unknown key {sec}.{key} ignored (typo?)")
        out: dict[str, Any] = {}
        for f in fields:
            if f.key in in_sec:
                out[f.key] = _coerce_value(f, in_sec[f.key])
                explicit.add((sec, f.key))
            else:
                out[f.key] = json.loads(json.dumps(f.default))
        res[sec] = out
    return res, explicit


def apply_security_bundle(p: KernelProfile) -> None:
    """Security knobs not explicitly set in the TOML follow the selected hardening bundle."""
    bundle = SECURITY_BUNDLES[p.g("security", "profile")]
    for key, val in bundle.items():
        if ("security", key) not in p.explicit:
            p.sections["security"][key] = val


def validate_profile(p: KernelProfile) -> None:
    for sec, fields in PROFILE_SPEC.items():
        for f in fields:
            val = p.sections[sec].get(f.key)
            if f.required and not val:
                raise ProfileError(f"{p.path.name}: missing required field {sec}.{f.key}")
            if f.choices and val not in f.choices:
                raise ProfileError(f"{p.path.name}: invalid value '{val}' for {sec}.{f.key}; allowed: {', '.join(map(str, f.choices))}")
            if f.kind == "int":
                if f.minimum is not None and val < f.minimum:
                    raise ProfileError(f"{p.path.name}: {sec}.{f.key}={val} below minimum {f.minimum}")
                if f.maximum is not None and val > f.maximum:
                    raise ProfileError(f"{p.path.name}: {sec}.{f.key}={val} above maximum {f.maximum}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", p.suffix):
        raise ProfileError(f"{p.path.name}: meta.suffix '{p.suffix}' must match [a-z0-9][a-z0-9-]*")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", p.name):
        raise ProfileError(f"{p.path.name}: meta.name '{p.name}' contains unsupported characters")


def normalize_profile(p: KernelProfile) -> list[str]:
    """Resolve dependent knobs deterministically; returns human-readable notices."""
    s = p.sections
    notes: list[str] = []

    def force(sec: str, key: str, val: Any, why: str) -> None:
        if s[sec][key] != val:
            notes.append(f"{sec}.{key}: {s[sec][key]} -> {val} ({why})")
            s[sec][key] = val

    if s["compiler"]["toolchain"] == "gcc":
        force("compiler", "lto", "none", "LTO requires LLVM")
        force("compiler", "kcfi", False, "kCFI requires clang")
        force("compiler", "fdo", "none", "AutoFDO/Propeller require clang")
        force("compiler", "polly", False, "Polly requires clang/LLVM")
    if s["compiler"]["lto"] != "thin":
        force("compiler", "thinlto_cache", False, "ThinLTO cache only applies to lto=thin")
    if s["timing"]["preempt"] == "rt":
        force("timing", "preempt_dynamic", False, "PREEMPT_RT excludes PREEMPT_DYNAMIC")
    if s["scheduler"]["type"] == "bmq":
        force("scheduler", "scx", "none", "Project C replaces EEVDF; sched_ext unavailable")
        force("scheduler", "scx_enable_class", False, "Project C replaces EEVDF; sched_ext unavailable")
    if s["scheduler"]["scx"] != "none":
        force("scheduler", "scx_enable_class", True, "a BPF scheduler daemon needs SCHED_CLASS_EXT")
    if s["memory"]["swap_backend"] != "zram":
        force("memory", "zram_multi_comp", False, "no zram device configured")
    if s["memory"]["thp"] == "never":
        force("memory", "thp_defrag", "never", "THP disabled")
    if not s["memory"]["numa"]:
        force("memory", "numa_balancing", False, "NUMA disabled")
    if not s["memory"]["ksm"]:
        force("memory", "ksm_run", False, "KSM not compiled")
    if s["memory"]["slub_tiny"]:
        force("memory", "slab_buckets", False, "SLAB_BUCKETS depends on !SLUB_TINY")
    if s["compiler"]["headers"] != "never" and s["memory"]["trim_unused_ksyms"]:
        force("memory", "trim_unused_ksyms", False, "TRIM_UNUSED_KSYMS breaks DKMS/out-of-tree modules; requires headers=never")
    if s["memory"]["footprint"] == "embedded":
        force("cpu", "compat32", False, "embedded footprint drops IA32 emulation")
        force("power", "hibernation", False, "embedded footprint drops hibernation")
    if s["cpu"]["mitigations"] == "off" and s["security"]["profile"] == "hardened":
        force("cpu", "mitigations", "on", "hardened profile keeps mitigations")
    if s["storage"]["io_scheduler"] == "bfq" and s["storage"]["iocost"]:
        notes.append("storage: bfq + iocost both active; iocost applies to devices without bfq")
    return notes


def cross_validate(p: KernelProfile, facts: "HostFacts | None" = None, *, force: bool = False) -> None:
    s = p.sections
    if s["meta"]["portable_package"] and s["cpu"]["arch"] == "native":
        raise ProfileError("portable_package=true forbids cpu.arch=native (use generic_v3/znver4/...)")
    if s["modules"]["mode"] == "strict" and not s["modules"]["modprobed_db"] and not s["modules"]["allow_lsmod_fallback"]:
        raise ProfileError("modules.mode=strict needs modprobed_db=true (or allow_lsmod_fallback=true)")
    if s["security"]["profile"] == "extreme" or s["cpu"]["mitigations"] == "off":
        if not s["security"]["acknowledge_risk"]:
            raise ProfileError("security.profile=extreme / cpu.mitigations=off require security.acknowledge_risk=true")
    if s["timing"]["hz"] not in HZ_CHOICES:
        raise ProfileError(f"timing.hz={s['timing']['hz']} unsupported")
    if s["release"]["pin"]:
        pinned = KVer.parse(s["release"]["pin"])
        if pinned is None:
            raise ProfileError(f"release.pin '{s['release']['pin']}' is not a kernel version")
        if pinned.key() < KVer(*MIN_KERNEL).key():
            raise ProfileError(f"release.pin {s['release']['pin']} is below the {MIN_KERNEL[0]}.{MIN_KERNEL[1]} floor")
    if facts is not None:
        if s["meta"]["bare_metal_only"] and facts.virt != "none" and not force:
            raise ProfileError(f"profile is bare_metal_only but this host is a '{facts.virt}' guest (use --force to override)")
        if s["cpu"]["nr_cpus"] and s["cpu"]["nr_cpus"] < facts.threads:
            warn(f"cpu.nr_cpus={s['cpu']['nr_cpus']} is below the host thread count ({facts.threads}); extra CPUs stay offline")
        if s["compiler"]["lto"] == "full" and facts.mem_gib + facts.swap_gib < 16:
            warn(f"Full LTO link needs ~16-24 GiB; host has {facts.mem_gib:.1f} GiB RAM + {facts.swap_gib:.1f} GiB swap. Consider lto=thin.")
        if s["memory"]["swap_backend"] == "zswap" and not facts.disk_swap:
            warn("zswap selected but no disk swap device is active; zswap needs a backing swap (or choose zram)")
        if s["compiler"]["headers"] == "never" and facts.dkms_modules:
            warn(f"headers=never but DKMS modules are installed: {', '.join(facts.dkms_modules)}")
        if s["timing"]["tickless"] == "full" and "nohz_full=" not in facts.cmdline:
            warn("tickless=full without nohz_full= on the command line adds overhead and no benefit")


# ---------------------------------------------------------------------------------------------------
# TOML emitter (stdlib only reads TOML; profiles are written with this self-documenting renderer)
# ---------------------------------------------------------------------------------------------------
def toml_scalar(v: Any) -> str:
    match v:
        case bool():
            return "true" if v else "false"
        case int():
            return str(v)
        case str():
            return json.dumps(v, ensure_ascii=False)
        case list() | tuple():
            return "[" + ", ".join(toml_scalar(x) for x in v) + "]"
        case _:
            return json.dumps(str(v))


def render_profile_toml(sections: Sections, *, header: str = "") -> str:
    lines: list[str] = [f"# generated by {APP_NAME} {APP_VERSION} -- {datetime.now(UTC).strftime('%Y-%m-%d')}"]
    if header:
        lines.append(f"# {header}")
    lines.append("")
    for sec, fields in PROFILE_SPEC.items():
        lines.append(f"[{sec}]")
        tables: list[tuple[str, dict[str, Any]]] = []
        for f in fields:
            val = sections.get(sec, {}).get(f.key, f.default)
            if f.kind == "table":
                tables.append((f.key, dict(val or {})))
                continue
            ctx = f" | choices: {' | '.join(map(str, f.choices))}" if f.choices else ""
            lines.append(f"# {f.help}{ctx}")
            lines.append(f"{f.key} = {toml_scalar(val)}")
        lines.append("")
        for key, tbl in tables:
            lines.append(f"[{sec}.{key}]")
            lines.append("# SYMBOL = true | false | \"m\" | 123 | \"string\"   (CONFIG_ prefix optional)")
            for k, v in tbl.items():
                lines.append(f"{k} = {toml_scalar(v)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def profile_from_tweaks(name: str, description: str, suffix: str, tweaks: Mapping[str, Mapping[str, Any]], priority: int = 50) -> KernelProfile:
    data: dict[str, dict[str, Any]] = {sec: dict(vals) for sec, vals in tweaks.items()}
    data.setdefault("meta", {}).update({"name": name, "description": description, "suffix": suffix, "priority": priority})
    sections, explicit = coerce(data, Path(f"{name}.toml"))
    p = KernelProfile(Path(f"{name}.toml"), sections, explicit)
    apply_security_bundle(p)
    return p


def load_profile(path: Path) -> KernelProfile:
    try:
        with open(path, "rb") as fp:
            raw = tomllib.load(fp)
    except tomllib.TOMLDecodeError as e:
        raise ProfileError(f"{path.name}: TOML parse error: {e}") from e
    except OSError as e:
        raise ProfileError(f"{path.name}: {e}") from e
    sections, explicit = coerce(raw, path)
    p = KernelProfile(path, sections, explicit)
    apply_security_bundle(p)
    validate_profile(p)
    return p


def profile_dirs() -> list[Path]:
    dirs = [PROFILES_DIR]
    if USER_PROFILES_DIR != PROFILES_DIR:
        dirs.append(USER_PROFILES_DIR)
    return dirs


def discover_profiles() -> list[KernelProfile]:
    profiles: dict[str, KernelProfile] = {}
    for d in profile_dirs():
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.toml")):
            try:
                p = load_profile(f)
            except DuskyError as e:
                warn(str(e))
                continue
            profiles[p.name] = p
    return sorted(profiles.values(), key=lambda x: (x.priority, x.name))


def ensure_profiles_exist() -> list[KernelProfile]:
    profiles = discover_profiles()
    if profiles:
        return profiles
    warn(f"No profiles found in {', '.join(str(d) for d in profile_dirs())}")
    if interactive() and ask_yes("Write the built-in default profiles now?", True):
        do_write_defaults(argparse.Namespace())
        return discover_profiles()
    raise ProfileError("No profiles available; run --write-default-profiles")


def print_profile_table(profiles: Sequence[KernelProfile], facts: "HostFacts | None" = None) -> None:
    rec = recommend_profile(profiles, facts) if facts else None
    rows = []
    for i, p in enumerate(profiles, 1):
        s = p.sections
        mark = f" {C.GREEN}★{C.RESET}" if rec is p else ""
        rows.append([str(i), p.name + mark, s["release"]["channel"], s["cpu"]["arch"], s["scheduler"]["type"] + ("+" + s["scheduler"]["scx"][4:] if s["scheduler"]["scx"] != "none" else ""),
                     str(s["timing"]["hz"]), s["timing"]["preempt"], s["memory"]["footprint"], s["compiler"]["lto"], s["modules"]["mode"][:3]])
    table(["#", "name", "channel", "arch", "sched", "hz", "preempt", "footprint", "lto", "mods"], rows)
    if rec is not None:
        note(f"★ recommended for this host: {rec.name}")


def recommend_profile(profiles: Sequence[KernelProfile], facts: "HostFacts") -> KernelProfile | None:
    names = {p.name: p for p in profiles}
    if facts.virt != "none" and "vm_guest" in names:
        return names["vm_guest"]
    if facts.mem_gib <= 4.5:
        for cand in ("minimal_strict", "embedded_lowram", "low_ram"):
            if cand in names:
                return names[cand]
    if facts.mem_gib <= 8.5 and "low_ram" in names:
        return names["low_ram"]
    if facts.battery and "battery_efficiency" in names:
        return names["battery_efficiency"]
    if facts.vendor == "amd" and facts.uarch in ("znver4", "znver5") and "zen4_zen5" in names:
        return names["zen4_zen5"]
    return names.get("dusky_personal") or (profiles[0] if profiles else None)


def select_profile(profiles: Sequence[KernelProfile], wanted: str | None, facts: "HostFacts | None" = None) -> KernelProfile:
    if wanted:
        for p in profiles:
            if p.name.lower() == wanted.lower():
                return p
        close = [p.name for p in profiles if wanted.lower() in p.name.lower()]
        hint = f" (did you mean: {', '.join(close)}?)" if close else ""
        raise ProfileError(f"No profile named '{wanted}'{hint}")
    if not interactive():
        raise ProfileError("Non-interactive session requires --profile NAME")
    rule("Select build profile")
    print_profile_table(profiles, facts)
    default = 1
    if facts is not None:
        rec = recommend_profile(profiles, facts)
        if rec is not None:
            default = list(profiles).index(rec) + 1
    return profiles[ask_index("Profile", len(profiles), default) - 1]


# ---------------------------------------------------------------------------------------------------
# CLI overrides + granular interactive wizard
# ---------------------------------------------------------------------------------------------------
@dataclass(slots=True)
class Overrides:
    cpu_arch: str | None = None
    modules_mode: str | None = None
    toolchain: str | None = None
    lto: str | None = None
    jobs: int | None = None
    pin: str | None = None
    channel: str | None = None
    scheduler: str | None = None
    scx: str | None = None
    headers: str | None = None
    footprint: str | None = None
    no_rust: bool = False

    @classmethod
    def from_env_and_args(cls, args: argparse.Namespace) -> Self:
        def env(name: str) -> str | None:
            v = os.environ.get(name, "").strip()
            return v or None

        jobs_env = env("DUSKY_JOBS")
        return cls(
            cpu_arch=getattr(args, "cpu_arch", None) or env("DUSKY_CPU_ARCH"),
            modules_mode=getattr(args, "modules_mode", None) or env("DUSKY_MODULES_MODE"),
            toolchain=getattr(args, "toolchain", None) or env("DUSKY_TOOLCHAIN"),
            lto=getattr(args, "lto", None) or env("DUSKY_LTO"),
            jobs=getattr(args, "jobs", None) or (int(jobs_env) if jobs_env and jobs_env.isdigit() else None),
            pin=getattr(args, "pin", None) or env("DUSKY_PIN"),
            channel=getattr(args, "channel", None) or env("DUSKY_CHANNEL"),
            scheduler=getattr(args, "scheduler", None) or env("DUSKY_SCHEDULER"),
            scx=getattr(args, "scx", None) or env("DUSKY_SCX"),
            headers=getattr(args, "headers", None) or env("DUSKY_HEADERS"),
            footprint=getattr(args, "footprint", None) or env("DUSKY_FOOTPRINT"),
            no_rust=bool(getattr(args, "no_rust", False)),
        )


def apply_overrides(p: KernelProfile, o: Overrides) -> list[str]:
    diff: list[str] = []

    def put(sec: str, key: str, val: Any) -> None:
        old = p.g(sec, key)
        if old != val:
            p.set(sec, key, val)
            diff.append(f"{sec}.{key}: {old} -> {val}")

    if o.cpu_arch:
        put("cpu", "arch", o.cpu_arch)
    if o.modules_mode:
        put("modules", "mode", o.modules_mode)
    if o.toolchain:
        put("compiler", "toolchain", o.toolchain)
    if o.lto:
        put("compiler", "lto", o.lto)
    if o.jobs:
        put("compiler", "jobs", o.jobs)
    if o.pin:
        put("release", "pin", o.pin)
    if o.channel:
        put("release", "channel", o.channel)
    if o.scheduler:
        put("scheduler", "type", o.scheduler)
    if o.scx:
        put("scheduler", "scx", o.scx)
    if o.headers:
        put("compiler", "headers", o.headers)
    if o.footprint:
        put("memory", "footprint", o.footprint)
    if o.no_rust:
        put("compiler", "rust", False)
    return diff


def field_relevant(s: Sections, sec: str, key: str) -> bool:
    """Skip questions whose answer cannot matter given earlier answers."""
    match (sec, key):
        case ("release", "allow_rc"):
            return s["release"]["channel"] == "mainline" or bool(s["release"]["pin"])
        case ("scheduler", "scx") | ("scheduler", "scx_enable_class"):
            return s["scheduler"]["type"] != "bmq"
        case ("scheduler", "scx_flags"):
            return s["scheduler"]["scx"] != "none"
        case ("scheduler", "require_patch") | ("scheduler", "allow_vanilla_fallback"):
            return s["scheduler"]["type"] != "eevdf"
        case ("cache", "llc_aggr_tolerance") | ("cache", "llc_aggr_cap"):
            return bool(s["cache"]["sched_cache"])
        case ("rseq", "slice_ext_nsec"):
            return bool(s["rseq"]["slice_extension"])
        case ("timing", "preempt_dynamic"):
            return s["timing"]["preempt"] != "rt"
        case ("memory", "thp_defrag") | ("memory", "thp_shmem"):
            return s["memory"]["thp"] != "never"
        case ("memory", "mglru_mask") | ("memory", "mglru_min_ttl_ms"):
            return bool(s["memory"]["mglru"])
        case ("memory", "zram_algo") | ("memory", "zram_size_pct") | ("memory", "zram_multi_comp"):
            return s["memory"]["swap_backend"] == "zram"
        case ("memory", "zram_recomp_algo"):
            return s["memory"]["swap_backend"] == "zram" and bool(s["memory"]["zram_multi_comp"])
        case ("memory", "zswap_compressor") | ("memory", "zswap_max_pool_pct"):
            return s["memory"]["swap_backend"] == "zswap"
        case ("memory", "numa_balancing") | ("memory", "nodes_shift"):
            return bool(s["memory"]["numa"])
        case ("memory", "ksm_run"):
            return bool(s["memory"]["ksm"])
        case ("memory", "slab_buckets"):
            return not s["memory"]["slub_tiny"]
        case ("memory", "trim_unused_ksyms"):
            return s["compiler"]["headers"] == "never"
        case ("compiler", "lto") | ("compiler", "kcfi") | ("compiler", "fdo"):
            return s["compiler"]["toolchain"] == "llvm"
        case ("compiler", "thinlto_cache") | ("compiler", "thinlto_cache_size_gb"):
            return s["compiler"]["toolchain"] == "llvm" and s["compiler"]["lto"] == "thin"
        case ("compiler", "fdo_profile_dir"):
            return s["compiler"]["fdo"] != "none"
        case ("security", "acknowledge_risk"):
            return s["security"]["profile"] == "extreme" or s["cpu"]["mitigations"] == "off"
        case ("modules", "modprobed_db_path") | ("modules", "allow_lsmod_fallback"):
            return bool(s["modules"]["modprobed_db"]) or s["modules"]["mode"] == "strict"
        case ("modules", "lmc_keep_extra"):
            return s["modules"]["mode"] == "expanded"
        case ("boot", "cmdline_extra") | ("boot", "write_entries"):
            return True
        case _:
            return True


def _fmt_value(spec: FieldSpec, val: Any) -> str:
    match spec.kind:
        case "bool":
            return "yes" if val else "no"
        case "list":
            return "[" + ", ".join(map(str, val)) + "]" if val else "[]"
        case "table":
            return ", ".join(f"{k}={v}" for k, v in val.items()) if val else "(none)"
        case _:
            return str(val) if str(val) != "" else "(empty)"


def _parse_answer(spec: FieldSpec, raw: str, current: Any) -> Any:
    match spec.kind:
        case "bool":
            low = raw.lower()
            if low in ("y", "yes", "true", "1", "on"):
                return True
            if low in ("n", "no", "false", "0", "off"):
                return False
            raise ValueError("answer y or n")
        case "int":
            if not re.fullmatch(r"-?\d+", raw):
                raise ValueError("enter an integer")
            val = int(raw)
            if spec.choices and val not in spec.choices:
                if 1 <= val <= len(spec.choices):
                    return spec.choices[val - 1]
                raise ValueError(f"allowed: {', '.join(map(str, spec.choices))}")
            if spec.minimum is not None and val < spec.minimum:
                raise ValueError(f"minimum is {spec.minimum}")
            if spec.maximum is not None and val > spec.maximum:
                raise ValueError(f"maximum is {spec.maximum}")
            return val
        case "list":
            if raw in ("-", "[]", "none"):
                return []
            return [x.strip() for x in raw.split(",") if x.strip()]
        case "table":
            out = dict(current)
            for item in (x.strip() for x in raw.split(",") if x.strip()):
                if item.startswith("-"):
                    out.pop(item[1:].removeprefix("CONFIG_"), None)
                    continue
                if "=" not in item:
                    raise ValueError("use SYMBOL=y|n|m|<int>|\"str\" or -SYMBOL to remove")
                sym, _, v = item.partition("=")
                sym = sym.strip().removeprefix("CONFIG_")
                v = v.strip()
                if v in ("y", "true"):
                    out[sym] = True
                elif v in ("n", "false"):
                    out[sym] = False
                elif re.fullmatch(r"-?\d+", v):
                    out[sym] = int(v)
                else:
                    out[sym] = v.strip('"')
            return out
        case _:
            if spec.choices:
                if raw in spec.choices:
                    return raw
                if raw.isdigit() and 1 <= int(raw) <= len(spec.choices):
                    return spec.choices[int(raw) - 1]
                raise ValueError(f"allowed: {', '.join(map(str, spec.choices))}")
            return raw


class WizardSignal(StrEnum):
    SKIP_SECTION = "s"
    ACCEPT_REST = "!"
    BACK = "b"
    MENU = "m"


def prompt_field(p: KernelProfile, sec: str, spec: FieldSpec, facts: "HostFacts | None") -> str | WizardSignal | None:
    """Ask one question. Returns a diff line, a WizardSignal, or None (kept default)."""
    current = p.g(sec, spec.key)
    say("")
    say(f"  {C.BOLD}{spec.help}{C.RESET}  {C.DIM}[{sec}.{spec.key}]{C.RESET}")
    say(f"    current: {C.CYAN}{_fmt_value(spec, current)}{C.RESET}")
    help_map = CHOICE_HELP.get((sec, spec.key), {})
    if spec.choices:
        for i, ch in enumerate(spec.choices, 1):
            ctx = help_map.get(str(ch), "")
            if (sec, spec.key) == ("cpu", "arch") and str(ch) in UARCH_INFO and str(ch) not in help_map:
                level, sym = UARCH_INFO[str(ch)]
                ctx = f"-march={ch} via KCFLAGS (psABI v{level}; {sym} when the more-uarches patch is present)"
            marker = "●" if ch == current else "○"
            say(f"    {marker} {i:>2}) {C.BOLD}{ch}{C.RESET}  {C.DIM}{ctx}{C.RESET}")
        if (sec, spec.key) == ("cpu", "arch") and facts is not None:
            note(f"    host: {facts.model} -> detected uarch '{facts.uarch or 'unknown'}', psABI v{facts.psabi_level}, {facts.threads} threads")
    elif spec.kind == "bool":
        say(f"    {C.DIM}y/n{C.RESET}")
    elif spec.kind == "int":
        rng = f"{spec.minimum if spec.minimum is not None else '-inf'}..{spec.maximum if spec.maximum is not None else 'inf'}"
        say(f"    {C.DIM}integer in {rng}{C.RESET}")
    elif spec.kind == "list":
        say(f"    {C.DIM}comma-separated values, '-' clears{C.RESET}")
    elif spec.kind == "table":
        say(f"    {C.DIM}SYMBOL=y|n|m|<int>|\"str\" comma-separated, -SYMBOL removes{C.RESET}")
    if (sec, spec.key) == ("memory", "footprint") and facts is not None:
        note(f"    host RAM: {facts.mem_gib:.1f} GiB -> suggested tier: {suggest_footprint(facts.mem_gib)}")
    while True:
        raw = ask("Enter keeps current | value | 'b' back | 's' skip section | '!' accept rest | 'm' jump to section", "")
        if raw == "":
            return None
        if raw in ("b", "back"):
            return WizardSignal.BACK
        if raw in ("s", "skip"):
            return WizardSignal.SKIP_SECTION
        if raw in ("!", "accept"):
            return WizardSignal.ACCEPT_REST
        if raw in ("m", "menu", "jump"):
            return WizardSignal.MENU
        try:
            val = _parse_answer(spec, raw, current)
        except ValueError as e:
            warn(f"Invalid: {e}")
            continue
        if val == current:
            note("    unchanged")
            return None
        p.set(sec, spec.key, val)
        line = f"{sec}.{spec.key}: {_fmt_value(spec, current)} -> {_fmt_value(spec, val)}"
        say(f"    {C.YELLOW}↳ {line}{C.RESET}")
        return line


def suggest_footprint(mem_gib: float) -> str:
    if mem_gib <= 3.5:
        return "embedded"
    if mem_gib <= 6:
        return "minimal"
    if mem_gib <= 8.5:
        return "lean"
    return "standard"


def run_wizard(p: KernelProfile, facts: "HostFacts | None", steps: Sequence[int] | None = None) -> list[str]:
    """Granular questionnaire over tunable knobs with per-question backward navigation ('b'), skip ('s'), jump ('m'), and accept ('!')."""
    order = list(steps) if steps else list(range(len(WIZARD_STEPS)))
    say("")
    info("Wizard controls: Enter = keep current, value = new value, 'b' = previous question, 's' = skip section, '!' = accept rest, 'm' = jump to section")

    active_questions: list[tuple[int, str, str, FieldSpec]] = []
    for step_idx in order:
        step = WIZARD_STEPS[step_idx]
        for sec, keys in step.groups:
            specs = {f.key: f for f in PROFILE_SPEC[sec]}
            for key in keys:
                spec = specs[key]
                if spec.wizard:
                    active_questions.append((step_idx, step.title, sec, spec))

    pos = 0
    history: list[tuple[int, str, str, Any, str | None]] = []
    diff_map: dict[str, str] = {}
    current_step_idx = -1

    while pos < len(active_questions):
        step_idx, step_title, sec, spec = active_questions[pos]
        if not field_relevant(p.sections, sec, spec.key):
            pos += 1
            continue

        if step_idx != current_step_idx:
            current_step_idx = step_idx
            rule(f"Wizard {step_idx + 1}/{len(WIZARD_STEPS)} · {step_title}")

        old_val = p.g(sec, spec.key)
        res = prompt_field(p, sec, spec, facts)

        if isinstance(res, WizardSignal):
            if res is WizardSignal.BACK:
                if history:
                    prev_pos, prev_sec, prev_key, prev_old_val, _ = history.pop()
                    p.set(prev_sec, prev_key, prev_old_val)
                    diff_map.pop(f"{prev_sec}.{prev_key}", None)
                    prev_spec = active_questions[prev_pos][3]
                    warn(f"Reverted {prev_sec}.{prev_key} to {_fmt_value(prev_spec, prev_old_val)} and stepped back.")
                    pos = prev_pos
                    current_step_idx = -1
                    continue
                else:
                    warn("Already at the very first question in the wizard.")
                    continue
            elif res is WizardSignal.SKIP_SECTION:
                cur_s = step_idx
                while pos < len(active_questions) and active_questions[pos][0] == cur_s:
                    pos += 1
                continue
            elif res is WizardSignal.ACCEPT_REST:
                info("Accepting profile defaults for all remaining sections")
                break
            elif res is WizardSignal.MENU:
                say("")
                say(f"{C.ACCENT}  Wizard sections:{C.RESET}")
                for i, st in enumerate(WIZARD_STEPS, 1):
                    marker = "▶" if (i - 1) == step_idx else " "
                    say(f"  {marker} {i:>2}) {st.title}")
                target_step = ask_index("Jump to section", len(WIZARD_STEPS), step_idx + 1) - 1
                found = False
                for idx, (s_idx, _, _, _) in enumerate(active_questions):
                    if s_idx == target_step:
                        pos = idx
                        current_step_idx = -1
                        found = True
                        break
                if found:
                    continue
                else:
                    warn(f"Section '{WIZARD_STEPS[target_step].title}' has no active questions in this pass.")
                    continue

        diff_line = res if isinstance(res, str) else None
        if diff_line:
            diff_map[f"{sec}.{spec.key}"] = diff_line
        history.append((pos, sec, spec.key, old_val, diff_line))
        pos += 1

    return list(diff_map.values())


def wizard_review_loop(p: KernelProfile, facts: "HostFacts | None", diff: list[str], *, force: bool) -> list[str]:
    """Validate after the wizard; let the user revisit an offending step instead of aborting."""
    while True:
        notes = normalize_profile(p)
        for n in notes:
            note(f"auto-adjusted {n}")
        try:
            validate_profile(p)
            cross_validate(p, facts, force=force)
            return diff + notes
        except ProfileError as e:
            err(str(e))
            if not interactive():
                raise
            raw = ask("Revisit wizard step number (1-11) or 'q' to abort", "q")
            if raw.lower() == "q":
                raise AbortError("Aborted in wizard") from e
            if raw.isdigit() and 1 <= int(raw) <= len(WIZARD_STEPS):
                diff.extend(run_wizard(p, facts, [int(raw) - 1]))


def offer_save_profile(p: KernelProfile) -> None:
    if not interactive() or ASSUME_YES:
        return
    if not ask_yes("Save these overrides as a new profile TOML?", False):
        return
    name = ask("New profile name", f"{p.name}_custom")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        warn("Invalid profile name; not saved")
        return
    suffix = ask("LOCALVERSION suffix (pkgbase becomes linux-<suffix>)", f"dusky-{name.replace('_', '-')}"[:40])
    sections = json.loads(json.dumps(p.sections))
    sections["meta"]["name"] = name
    sections["meta"]["suffix"] = suffix.strip("-")
    sections["meta"]["description"] = f"Derived from {p.name} via wizard"
    dest_dir = USER_PROFILES_DIR if not os.access(PROFILES_DIR, os.W_OK) else PROFILES_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.toml"
    dest.write_text(render_profile_toml(sections, header=f"derived from {p.name}"), encoding="utf-8")
    ok(f"Saved profile: {dest}")


def configure_profile_interactively(p: KernelProfile, facts: "HostFacts | None", args: argparse.Namespace) -> list[str]:
    """Top-level gate: use defaults exactly, or enter the granular wizard."""
    cli_diff = apply_overrides(p, Overrides.from_env_and_args(args))
    force_wizard = bool(getattr(args, "wizard", False))
    skip_wizard = bool(getattr(args, "no_prompt", False)) or ASSUME_YES or not interactive()
    diff = list(cli_diff)
    if force_wizard or not skip_wizard:
        use_defaults = True if skip_wizard and not force_wizard else ask_yes(
            f"Do you want to use the profile defaults exactly as configured in '{p.name}'?", True)
        if force_wizard or not use_defaults:
            diff.extend(run_wizard(p, facts))
            diff = wizard_review_loop(p, facts, diff, force=bool(getattr(args, "force", False)))
            if diff:
                offer_save_profile(p)
            return diff
    notes = normalize_profile(p)
    validate_profile(p)
    cross_validate(p, facts, force=bool(getattr(args, "force", False)))
    return diff + notes

# ---------------------------------------------------------------------------------------------------
# Process execution: every child runs in its own process group so aborts never leak make/clang trees
# ---------------------------------------------------------------------------------------------------
_CHILD_LOCK: Final = threading.Lock()
_CHILD_PGIDS: Final[set[int]] = set()
_ABORT: Final = threading.Event()


def terminate_process_group(pgid: int, grace: float = 2.0) -> None:
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _reap_all() -> None:
    with _CHILD_LOCK:
        pgids = list(_CHILD_PGIDS)
        _CHILD_PGIDS.clear()
    for pgid in pgids:
        terminate_process_group(pgid)


def _register(proc: subprocess.Popen[Any], own_group: bool) -> int | None:
    if not own_group:
        return None
    with _CHILD_LOCK:
        _CHILD_PGIDS.add(proc.pid)
    return proc.pid


def _unregister(pgid: int | None) -> None:
    if pgid is not None:
        with _CHILD_LOCK:
            _CHILD_PGIDS.discard(pgid)


def _on_signal(signum: int, _frame: Any) -> None:
    _ABORT.set()
    _reap_all()
    sys.stdout.write(C.SHOW)
    sys.stdout.flush()
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    raise SystemExit(128 + signum)


def install_signal_handlers() -> None:
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass


def check_abort() -> None:
    if _ABORT.is_set():
        raise AbortError("Aborted")


def run(cmd: Sequence[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None, check: bool = True,
        timeout: float | None = None, capture: bool = True, own_group: bool = True, stdin_null: bool = True) -> subprocess.CompletedProcess[str]:
    check_abort()
    debug("run: " + shlex.join(cmd))
    JOURNAL.write("$ " + shlex.join(cmd))
    pgid: int | None = None
    try:
        proc = subprocess.Popen(list(cmd), cwd=cwd, env=dict(env) if env is not None else None, text=True, encoding="utf-8", errors="replace",
                                stdin=subprocess.DEVNULL if stdin_null else None,
                                stdout=subprocess.PIPE if capture else None, stderr=subprocess.STDOUT if capture else None,
                                start_new_session=own_group)
    except FileNotFoundError as e:
        raise DependencyError(f"Executable not found: {cmd[0]}") from e
    pgid = _register(proc, own_group)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        if pgid is not None:
            terminate_process_group(pgid)
        proc.wait()
        raise BuildError(f"Command timed out after {timeout}s: {shlex.join(cmd)}") from e
    finally:
        _unregister(pgid)
    out = out or ""
    if out and capture:
        JOURNAL.write(out.rstrip())
    if check and proc.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-25:])
        raise BuildError(f"Command failed (exit {proc.returncode}): {shlex.join(cmd)}\n{tail}")
    return subprocess.CompletedProcess(list(cmd), proc.returncode, out, "")


def run_stream(cmd: Sequence[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None,
               on_line: Callable[[str], None] | None = None) -> int:
    check_abort()
    JOURNAL.write("$ " + shlex.join(cmd))
    proc = subprocess.Popen(list(cmd), cwd=cwd, env=dict(env) if env is not None else None, text=True, encoding="utf-8", errors="replace",
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True, bufsize=1)
    pgid = _register(proc, True)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.rstrip("\r\n")
            JOURNAL.write(text)
            if on_line is not None:
                on_line(text)
            if _ABORT.is_set():
                terminate_process_group(proc.pid)
                break
        proc.stdout.close()
        return proc.wait()
    finally:
        _unregister(pgid)


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def tool_version(cmd: Sequence[str]) -> str:
    try:
        cp = run(list(cmd), check=False, timeout=20)
    except DuskyError:
        return ""
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", cp.stdout or "")
    return m.group(1) if m else ""


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


class Privilege:
    """sudo/doas/run0 front-end with a credential keep-alive during long phases."""

    def __init__(self) -> None:
        self.tool: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def detect(self) -> str:
        if self.tool:
            return self.tool
        if os.geteuid() == 0:
            self.tool = "root"
        elif have("sudo"):
            self.tool = "sudo"
        elif have("doas"):
            self.tool = "doas"
        elif have("run0"):
            self.tool = "run0"
        else:
            raise DependencyError("No privilege escalation tool found (sudo, doas or run0)")
        return self.tool

    def ensure(self) -> None:
        tool = self.detect()
        if tool != "sudo" or self._thread is not None:
            return
        info("Privileged steps ahead (sudo)")
        try:
            subprocess.run(["sudo", "-v"], check=True)
        except (subprocess.CalledProcessError, OSError) as e:
            raise DependencyError("sudo credentials required") from e
        self._thread = threading.Thread(target=self._keepalive, name="sudo-keepalive", daemon=True)
        self._thread.start()

    def _keepalive(self) -> None:
        while not self._stop.wait(50):
            subprocess.run(["sudo", "-nv"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def argv(self, cmd: Sequence[str]) -> list[str]:
        match self.detect():
            case "root":
                return list(cmd)
            case "sudo":
                return ["sudo", *cmd]
            case "doas":
                return ["doas", *cmd]
            case _:
                return ["run0", "--background=", *cmd]

    def run(self, cmd: Sequence[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
        self.ensure()
        return run(self.argv(cmd), check=check, capture=capture, own_group=False, stdin_null=False)

    def write_files(self, files: Mapping[Path, tuple[str, str]]) -> None:
        """Install {dest: (content, mode)} atomically with one privileged shell invocation."""
        if not files:
            return
        self.ensure()
        with tempfile.TemporaryDirectory(prefix="dusky-stage-") as tmp:
            staged: list[tuple[Path, Path, str]] = []
            for i, (dest, (content, mode)) in enumerate(files.items()):
                src = Path(tmp) / f"{i:03d}-{dest.name}"
                src.write_text(content, encoding="utf-8")
                staged.append((src, dest, mode))
            script = "set -e\n" + "\n".join(f"install -Dm{mode} {shlex.quote(str(src))} {shlex.quote(str(dest))}" for src, dest, mode in staged) + "\n"
            (Path(tmp) / "install.sh").write_text(script, encoding="utf-8")
            os.chmod(Path(tmp), 0o755)
            for src, _, _ in staged:
                os.chmod(src, 0o644)
            self.run(["sh", str(Path(tmp) / "install.sh")])

    def stop(self) -> None:
        self._stop.set()
        if self.tool == "sudo":
            subprocess.run(["sudo", "-k"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


PRIV: Final = Privilege()


# ---------------------------------------------------------------------------------------------------
# Live build monitor
# ---------------------------------------------------------------------------------------------------
_KBUILD_STEP_RE: Final = re.compile(r"^\s{2}([A-Z][A-Z0-9_]+)(?:\s\[[MA]\])?\s+(\S.*)$")


class Live:
    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str, expected_steps: int | None, expected_seconds: float | None) -> None:
        self.label = label
        self.expected_steps = expected_steps
        self.expected_seconds = expected_seconds
        self.steps = 0
        self.phase = "configure"
        self.last = ""
        self.errors: list[str] = []
        self.tail: collections.deque[str] = collections.deque(maxlen=40)
        self.start = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick = 0
        self.tty = sys.stdout.isatty()

    def feed(self, line: str) -> None:
        self.tail.append(line)
        m = _KBUILD_STEP_RE.match(line)
        if m:
            self.steps += 1
            tag, target = m.group(1), m.group(2)
            self.last = f"{tag} {target}"[-70:]
            if tag in ("CC", "RUSTC", "AS") and self.phase in ("configure", "packaging"):
                self.phase = "compile"
            if tag in ("LTO", "LD") and target.startswith("vmlinux"):
                self.phase = "link vmlinux" + (" (LTO)" if tag == "LTO" or "vmlinux.o" in target else "")
            elif tag == "BTF":
                self.phase = "BTF generation"
            elif tag == "MODPOST":
                self.phase = "modpost"
            elif tag in ("INSTALL", "STRIP", "SIGN", "ZSTD", "XZ", "GZIP") and "modules" in target or tag == "DEPMOD":
                self.phase = "modules_install"
        elif line.startswith("==>"):
            self.phase = "packaging: " + line[4:60].strip()
        low = line.lower()
        if ("error:" in low or " error " in low or low.startswith("make: ***") or "undefined reference" in low or "Error " in line) and len(self.errors) < 40:
            self.errors.append(line.strip()[:200])

    def _status(self) -> str:
        elapsed = time.monotonic() - self.start
        parts = [f"{C.ACCENT}{self.SPIN[self._tick % len(self.SPIN)]}{C.RESET} {self.label}", fmt_duration(elapsed), f"{self.steps:,} steps", self.phase]
        if self.expected_steps and self.expected_seconds and self.steps > 50:
            frac = min(0.98, self.steps / max(1, self.expected_steps))
            eta = max(0.0, self.expected_seconds - elapsed) if frac < 0.5 else max(0.0, elapsed / frac - elapsed)
            parts.append(f"ETA ~{fmt_duration(eta)}")
        if self.last:
            parts.append(C.DIM + self.last + C.RESET)
        s = " │ ".join(parts)
        w = term_width()
        while visible_len(s) > w - 1 and self.last:
            self.last = self.last[:-8]
            parts[-1] = C.DIM + self.last + C.RESET
            s = " │ ".join(parts)
        return s

    def _loop(self) -> None:
        while not self._stop.wait(0.5):
            self._tick += 1
            with _OUT_LOCK:
                if self.tty:
                    sys.stdout.write("\r" + C.CLEAR_EOL + self._status())
                    sys.stdout.flush()

    def emit(self, text: str) -> None:
        if self.tty:
            sys.stdout.write("\r" + C.CLEAR_EOL + text + "\n" + self._status())
        else:
            sys.stdout.write(text + "\n")
        sys.stdout.flush()

    def __enter__(self) -> Self:
        global _LIVE
        _LIVE = self
        if self.tty:
            sys.stdout.write(C.HIDE)
        self._thread = threading.Thread(target=self._loop, name="live-status", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        global _LIVE
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        _LIVE = None
        if self.tty:
            sys.stdout.write("\r" + C.CLEAR_EOL + C.SHOW)
            sys.stdout.flush()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start


def load_history() -> list[Json]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def record_history(entry: Json) -> None:
    hist = load_history()
    hist.append(entry)
    hist = hist[-200:]
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    except OSError:
        pass


def history_estimate(profile: str, lto: str) -> tuple[int | None, float | None]:
    for entry in reversed(load_history()):
        if entry.get("profile") == profile and entry.get("lto") == lto and entry.get("success"):
            return int(entry.get("steps") or 0) or None, float(entry.get("duration") or 0) or None
    for entry in reversed(load_history()):
        if entry.get("success") and entry.get("lto") == lto:
            return int(entry.get("steps") or 0) or None, float(entry.get("duration") or 0) or None
    return None, None


# ---------------------------------------------------------------------------------------------------
# Host telemetry
# ---------------------------------------------------------------------------------------------------
def _read(path: str | Path, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default


def _cpuinfo_flags() -> frozenset[str]:
    for line in _read("/proc/cpuinfo").splitlines():
        if line.startswith("flags"):
            return frozenset(line.split(":", 1)[1].split())
    return frozenset()


def psabi_level(flags: frozenset[str]) -> int:
    v2 = {"cx16", "lahf_lm", "popcnt", "sse4_1", "sse4_2", "ssse3"}
    v3 = {"avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe", "xsave"}
    v4 = {"avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"}
    if v4 <= flags and v3 <= flags:
        return 4
    if v3 <= flags and v2 <= flags:
        return 3
    if v2 <= flags:
        return 2
    return 1


def detect_native_uarch() -> str:
    """Ask the compiler what -march=native resolves to; map onto our vocabulary."""
    candidates: list[str] = []
    if have("clang"):
        cp = run(["clang", "-march=native", "-E", "-", "-###"], check=False, timeout=30)
        m = re.search(r'"-target-cpu"\s+"([^"]+)"', cp.stdout or "")
        if m:
            candidates.append(m.group(1).lower())
    if have("gcc"):
        cp = run(["gcc", "-march=native", "-Q", "--help=target"], check=False, timeout=30)
        m = re.search(r"-march=\s+(\S+)", cp.stdout or "")
        if m:
            candidates.append(m.group(1).lower())
    aliases = {"icelake": "icelake-client", "skylake-avx512": "skylake-avx512", "cascadelake": "skylake-avx512", "cooperlake": "skylake-avx512",
               "goldmont-plus": "generic_v2", "tremont": "generic_v2", "gracemont": "alderlake", "sierraforest": "alderlake", "pantherlake": "lunarlake",
               "diamondrapids": "graniterapids", "znver6": "znver5", "x86-64": "generic", "x86-64-v2": "generic_v2", "x86-64-v3": "generic_v3", "x86-64-v4": "generic_v4"}
    for c in candidates:
        c = aliases.get(c, c)
        if c in CPU_ARCHES:
            return c
    return ""


def _sys_llc() -> tuple[int, int]:
    domains: set[str] = set()
    size_kib = 0
    base = Path("/sys/devices/system/cpu")
    for cpu in base.glob("cpu[0-9]*"):
        for idx in (cpu / "cache").glob("index*"):
            if _read(idx / "level").strip() == "3":
                domains.add(_read(idx / "shared_cpu_list").strip())
                m = re.match(r"(\d+)K", _read(idx / "size").strip())
                if m:
                    size_kib = max(size_kib, int(m.group(1)))
    return len(domains), size_kib


def _gpu_vendors() -> tuple[str, ...]:
    vendors: list[str] = []
    for dev in Path("/sys/bus/pci/devices").glob("*"):
        cls = _read(dev / "class").strip()
        if not cls.startswith("0x03"):
            continue
        vid = _read(dev / "vendor").strip().lower()
        name = {"0x1002": "amd", "0x10de": "nvidia", "0x8086": "intel", "0x1af4": "virtio", "0x15ad": "vmware", "0x1b36": "qxl", "0x1234": "bochs"}.get(vid, vid)
        if name not in vendors:
            vendors.append(name)
    return tuple(vendors)


def _mounted_filesystems() -> tuple[tuple[str, ...], str]:
    fstypes: list[str] = []
    root_fs = ""
    for line in _read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mnt, fstype = parts[1], parts[2]
        if fstype in ("ext4", "btrfs", "xfs", "f2fs", "vfat", "exfat", "ntfs3", "fuse", "fuseblk", "overlay", "nfs", "nfs4", "cifs", "smb3", "erofs", "zfs", "bcachefs"):
            if fstype not in fstypes:
                fstypes.append(fstype)
            if mnt == "/":
                root_fs = fstype
    for line in _read("/etc/fstab").splitlines():
        parts = line.split()
        if len(parts) >= 3 and not line.lstrip().startswith("#") and parts[2] not in fstypes and parts[2] in ("ext4", "btrfs", "xfs", "f2fs", "vfat", "exfat", "ntfs3", "nfs", "nfs4", "cifs"):
            fstypes.append(parts[2])
    return tuple(fstypes), root_fs


def _dkms_modules() -> tuple[str, ...]:
    mods: list[str] = []
    dkms_dir = Path("/var/lib/dkms")
    if dkms_dir.is_dir():
        mods.extend(sorted(p.name for p in dkms_dir.iterdir() if p.is_dir()))
    if have("pacman"):
        cp = run(["pacman", "-Qq"], check=False, timeout=30)
        for line in (cp.stdout or "").splitlines():
            if line.endswith("-dkms") and line not in mods:
                mods.append(line)
    return tuple(mods)


def _bootloaders() -> tuple[tuple[str, ...], str, str]:
    found: list[str] = []
    esp = xbootldr = ""
    if have("bootctl"):
        esp_out = (run(["bootctl", "-p"], check=False, timeout=20).stdout or "").strip()
        xbootldr_out = (run(["bootctl", "-x"], check=False, timeout=20).stdout or "").strip()
        if esp_out.startswith("/"):
            esp = esp_out
        if xbootldr_out.startswith("/"):
            xbootldr = xbootldr_out
        cp = run(["bootctl", "is-installed"], check=False, timeout=20)
        has_sdboot_efivars = False
        try:
            efivars = Path("/sys/firmware/efi/efivars")
            if efivars.is_dir():
                has_sdboot_efivars = any(efivars.glob("LoaderInfo-*")) or any(efivars.glob("LoaderEntry*"))
        except Exception:
            pass
        if cp.returncode == 0 or has_sdboot_efivars or esp:
            found.append("systemd-boot")
    if "systemd-boot" not in found:
        for cand in ("/boot/loader/entries", "/efi/loader/entries", "/boot/efi/loader/entries"):
            if Path(cand).is_dir():
                found.append("systemd-boot")
                esp = esp or str(Path(cand).parent.parent)
                break
    if Path("/boot/grub/grub.cfg").is_file() or (have("grub-mkconfig") and Path("/boot/grub").is_dir()):
        found.append("grub")
    for cand in ("/boot/EFI/refind/refind.conf", "/efi/EFI/refind/refind.conf", "/boot/efi/EFI/refind/refind.conf"):
        if Path(cand).is_file():
            found.append("refind")
            break
    for cand in ("/boot/limine.conf", "/boot/EFI/limine/limine.conf", "/efi/limine.conf"):
        if Path(cand).is_file():
            found.append("limine")
            break
    return tuple(found), esp, xbootldr


def _tool_versions() -> dict[str, str]:
    probes = {"clang": ["clang", "--version"], "ld.lld": ["ld.lld", "--version"], "llvm-ar": ["llvm-ar", "--version"], "gcc": ["gcc", "--version"],
              "rustc": ["rustc", "--version"], "bindgen": ["bindgen", "--version"], "pahole": ["pahole", "--version"], "make": ["make", "--version"],
              "makepkg": ["makepkg", "--version"], "mkinitcpio": ["mkinitcpio", "--version"], "perf": ["perf", "--version"],
              "create_llvm_prof": ["create_llvm_prof", "--version"], "gpg": ["gpg", "--version"], "curl": ["curl", "--version"], "aria2c": ["aria2c", "--version"]}
    out: dict[str, str] = {}
    for name, cmd in probes.items():
        out[name] = tool_version(cmd) or ("present" if have(name) else "") if have(name) else ""
    for name in ("modprobed-db", "scx_loader", "scx_lavd", "scx_bpfland", "zram-generator", "dkms", "kernel-install", "grub-mkconfig", "bootctl", "limine-update", "mkrlconf"):
        out[name] = "present" if (have(name) or Path(f"/usr/lib/systemd/system-generators/{name}").exists()) else ""
    return out


@dataclass(slots=True, kw_only=True)
class HostFacts:
    vendor: str
    model: str
    flags: frozenset[str]
    threads: int
    cores: int
    llc_domains: int
    llc_kib: int
    mem_gib: float
    swap_gib: float
    disk_swap: bool
    numa_nodes: int
    virt: str
    gpus: tuple[str, ...]
    psabi_level: int
    uarch: str
    kernel: str
    cmdline: str
    filesystems: tuple[str, ...]
    root_fs: str
    root_luks: bool
    has_nvme: bool
    rotational: bool
    battery: bool
    dkms_modules: tuple[str, ...]
    bootloaders: tuple[str, ...]
    esp: str
    xbootldr: str
    tools: dict[str, str]
    sched_ext_live: bool
    initrd_compression: str
    microcode_hook: bool

    def as_json(self) -> Json:
        d = {k: getattr(self, k) for k in self.__slots__}
        d["flags"] = sorted(self.flags)
        return d


@functools.cache
def host_facts() -> HostFacts:
    cpuinfo = _read("/proc/cpuinfo")
    vendor_id = re.search(r"^vendor_id\s*:\s*(\S+)", cpuinfo, re.M)
    vendor = {"AuthenticAMD": "amd", "GenuineIntel": "intel"}.get(vendor_id.group(1) if vendor_id else "", "other")
    model_m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
    cores = len({(m.group(1), m.group(2)) for m in re.finditer(r"physical id\s*:\s*(\d+)\n(?:.*\n)*?core id\s*:\s*(\d+)", cpuinfo)}) or (os.cpu_count() or 1)
    meminfo = _read("/proc/meminfo")

    def mem_kib(key: str) -> int:
        m = re.search(rf"^{key}:\s+(\d+)", meminfo, re.M)
        return int(m.group(1)) if m else 0

    swaps = [line.split()[0] for line in _read("/proc/swaps").splitlines()[1:] if line.strip()]
    virt = "none"
    if have("systemd-detect-virt"):
        cp = run(["systemd-detect-virt"], check=False, timeout=10)
        virt = (cp.stdout or "none").strip() if cp.returncode == 0 else "none"
    llc_domains, llc_kib = _sys_llc()
    fstypes, root_fs = _mounted_filesystems()
    rotational = any(_read(p).strip() == "1" for p in Path("/sys/block").glob("sd*/queue/rotational"))
    flags = _cpuinfo_flags()
    mkconf = _read("/etc/mkinitcpio.conf")
    comp_m = re.search(r'^COMPRESSION="?(\w+)"?', mkconf, re.M)
    hooks_m = re.search(r"^HOOKS=\((.*)\)", mkconf, re.M)
    hooks = hooks_m.group(1).split() if hooks_m else []
    bootloaders, esp, xbootldr = _bootloaders()
    root_luks = any(line.split()[0].startswith("/dev/mapper/") for line in _read("/proc/mounts").splitlines() if len(line.split()) > 1 and line.split()[1] == "/")
    return HostFacts(
        vendor=vendor, model=(model_m.group(1).strip() if model_m else platform.processor() or "unknown"), flags=flags,
        threads=os.process_cpu_count() or os.cpu_count() or 1, cores=cores, llc_domains=llc_domains, llc_kib=llc_kib,
        mem_gib=mem_kib("MemTotal") / 1048576.0, swap_gib=mem_kib("SwapTotal") / 1048576.0,
        disk_swap=any(not s.startswith("/dev/zram") for s in swaps),
        numa_nodes=len(list(Path("/sys/devices/system/node").glob("node[0-9]*"))) or 1, virt=virt, gpus=_gpu_vendors(),
        psabi_level=psabi_level(flags), uarch=detect_native_uarch(), kernel=os.uname().release, cmdline=_read("/proc/cmdline").strip(),
        filesystems=fstypes, root_fs=root_fs, root_luks=root_luks, has_nvme=any(Path("/sys/block").glob("nvme*")), rotational=rotational,
        battery=any(Path("/sys/class/power_supply").glob("BAT*")), dkms_modules=_dkms_modules(), bootloaders=bootloaders, esp=esp, xbootldr=xbootldr,
        tools=_tool_versions(), sched_ext_live=Path("/sys/kernel/sched_ext").is_dir(),
        initrd_compression=(comp_m.group(1) if comp_m else "zstd"), microcode_hook="microcode" in hooks,
    )


def auto_jobs(facts: HostFacts, lto: str) -> int:
    per_job_gib = 1.5 if lto == "full" else 1.0
    return max(2, min(facts.threads, int(facts.mem_gib // per_job_gib) or 2))


# ---------------------------------------------------------------------------------------------------
# Kernel versions & releases (kernel.org releases.json)
# ---------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, order=False)
class KVer:
    major: int
    minor: int
    patch: int = 0
    rc: int | None = None

    @classmethod
    def parse(cls, text: str) -> "KVer | None":
        m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:-rc(\d+))?", text.strip())
        if not m:
            return None
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), int(m.group(4)) if m.group(4) else None)

    def key(self) -> tuple[int, int, int, int, int]:
        return (self.major, self.minor, self.patch, 0 if self.rc is not None else 1, self.rc or 0)

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}" + (f".{self.patch}" if self.patch else "")
        return base + (f"-rc{self.rc}" if self.rc is not None else "")


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    moniker: str
    released: str
    source_url: str
    pgp_url: str | None

    @property
    def is_rc(self) -> bool:
        return "-rc" in self.version

    @property
    def kver(self) -> KVer:
        return KVer.parse(self.version) or KVer(0, 0)

    @property
    def archive_name(self) -> str:
        return Path(urllib.parse.urlparse(self.source_url).path).name


def http_get(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NetworkError(f"GET {url} failed: {e}") from e


def cdn_url(version: str) -> str:
    major = version.split(".")[0]
    return f"https://cdn.kernel.org/pub/linux/kernel/v{major}.x/linux-{version}.tar.xz"


def fetch_releases() -> list[Release]:
    try:
        data = json.loads(http_get(KERNEL_ORG_RELEASES).decode("utf-8"))
    except (NetworkError, ValueError) as e:
        warn(f"kernel.org releases.json unavailable ({e}); only pinned versions can be resolved")
        return []
    res: list[Release] = []
    for r in data.get("releases", []):
        ver = str(r.get("version", ""))
        if not KVer.parse(ver):
            continue
        released = r.get("released", {}) or {}
        res.append(Release(version=ver, moniker=str(r.get("moniker", "stable")), released=str(released.get("isodate", "")),
                           source_url=str(r.get("source") or cdn_url(ver)), pgp_url=r.get("pgp") or None))
    return res


def candidates_for(releases: Sequence[Release], channel: str, allow_rc: bool, min_ver: str) -> list[Release]:
    floor = KVer.parse(min_ver) or KVer(*MIN_KERNEL)
    floor = max(floor, KVer(*MIN_KERNEL), key=lambda k: k.key())
    effective_allow_rc = allow_rc or (channel == "mainline")
    cands: list[Release] = []
    for r in releases:
        if r.is_rc and not effective_allow_rc:
            continue
        if r.kver.key() < floor.key():
            continue
        match channel:
            case "mainline":
                if r.moniker == "mainline":
                    cands.append(r)
            case "stable":
                if r.moniker == "stable" or (r.moniker == "mainline" and not r.is_rc):
                    cands.append(r)
            case "longterm":
                if r.moniker == "longterm":
                    cands.append(r)
    cands.sort(key=lambda r: r.kver.key(), reverse=True)
    return cands


def pinned_release(pin: str, releases: Sequence[Release]) -> Release:
    for r in releases:
        if r.version == pin:
            return r
    kv = KVer.parse(pin)
    if kv is None:
        raise ProfileError(f"release.pin '{pin}' is not a kernel version")
    if kv.rc is not None:
        return Release(pin, "mainline", "", f"https://git.kernel.org/torvalds/t/linux-{pin}.tar.gz", None)
    return Release(pin, "pinned", "", cdn_url(pin), cdn_url(pin).replace(".tar.xz", ".tar.sign"))


def choose_release(p: KernelProfile, releases: Sequence[Release]) -> Release:
    pin = p.g("release", "pin")
    if pin:
        rel = pinned_release(pin, releases)
        ok(f"Pinned release {rel.version}")
        return rel
    channel = p.g("release", "channel")
    cands = candidates_for(releases, channel, p.g("release", "allow_rc"), p.g("release", "min_version"))
    if not cands:
        raise NetworkError(f"No kernel >= {MIN_KERNEL[0]}.{MIN_KERNEL[1]} found in channel '{channel}' (allow_rc={p.g('release', 'allow_rc')})")
    if not interactive() or ASSUME_YES:
        return cands[0]
    rule(f"Select kernel release ({channel})")
    table(["#", "version", "moniker", "released"], [[str(i), r.version, r.moniker, r.released] for i, r in enumerate(cands[:12], 1)])
    return cands[ask_index("Release", min(12, len(cands)), 1) - 1]


# ---------------------------------------------------------------------------------------------------
# Tarballs: download (resumable), SHA256 (sha256sums.asc) and PGP (xz -cd | gpg --verify)
# ---------------------------------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(4 << 20):
            h.update(chunk)
    return h.hexdigest()


def expected_sha256(archive_name: str, version: str) -> str | None:
    major = version.split(".")[0]
    try:
        raw = http_get(f"https://cdn.kernel.org/pub/linux/kernel/v{major}.x/sha256sums.asc").decode("utf-8", "replace")
    except NetworkError:
        return None
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("*") == archive_name:
            return parts[0]
    return None


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    info(f"Downloading {url}")
    if have("aria2c"):
        cmd = ["aria2c", "--console-log-level=warn", "--summary-interval=0", "-x8", "-s8", "-k1M", "-c", "--auto-file-renaming=false",
               "-d", str(dest.parent), "-o", tmp.name, url]
    elif have("curl"):
        cmd = ["curl", "-fL", "--retry", "5", "--retry-all-errors", "-C", "-", "--progress-bar", "-A", USER_AGENT, "-o", str(tmp), url]
    else:
        raise DependencyError("Neither aria2c nor curl is installed (pacman -S curl)")
    cp = run(cmd, check=False, capture=False)
    if cp.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise NetworkError(f"Download failed ({cp.returncode}): {url}")
    tmp.replace(dest)


def ensure_kernel_keys() -> bool:
    if not have("gpg"):
        return False
    cp = run(["gpg", "--batch", "--list-keys", "--with-colons", *sorted(KERNEL_SIGNING_FPRS)], check=False, timeout=30)
    if cp.returncode == 0:
        return True
    info("Fetching kernel.org signing keys via WKD (torvalds@kernel.org, gregkh@kernel.org, sashal@kernel.org)")
    cp = run(["gpg", "--batch", "--locate-keys", "torvalds@kernel.org", "gregkh@kernel.org", "sashal@kernel.org"], check=False, timeout=120)
    return cp.returncode == 0


def verify_pgp(tarball: Path, pgp_url: str) -> bool | None:
    """True = valid signature by a kernel.org key, False = invalid, None = not verifiable (no gpg / no keys)."""
    if not ensure_kernel_keys():
        return None
    sig = tarball.with_name(Path(urllib.parse.urlparse(pgp_url).path).name)
    if not sig.is_file():
        try:
            sig.write_bytes(http_get(pgp_url))
        except NetworkError as e:
            warn(f"Signature download failed: {e}")
            return None
    decomp = ["xz", "-cd", str(tarball)] if tarball.suffix == ".xz" else ["gzip", "-cd", str(tarball)]
    xz = subprocess.Popen(decomp, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    gpg = subprocess.Popen(["gpg", "--batch", "--status-fd", "1", "--verify", str(sig), "-"], stdin=xz.stdout, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    pg1, pg2 = _register(xz, True), _register(gpg, True)
    try:
        assert xz.stdout is not None
        xz.stdout.close()
        out, _ = gpg.communicate()
        xz.wait()
    finally:
        _unregister(pg1)
        _unregister(pg2)
    fprs = {m.group(1) for m in re.finditer(r"\[GNUPG:\] VALIDSIG (\w+)", out or "")}
    if gpg.returncode != 0 or not fprs:
        return False
    return bool(fprs & KERNEL_SIGNING_FPRS)


def obtain_tarball(rel: Release, require_signature: bool) -> Path:
    TARBALL_DIR.mkdir(parents=True, exist_ok=True)
    dest = TARBALL_DIR / rel.archive_name
    if dest.is_file() and dest.stat().st_size > 0:
        ok(f"Using cached archive {dest.name} ({fmt_bytes(dest.stat().st_size)})")
    else:
        download(rel.source_url, dest)
    verified = False
    if rel.pgp_url:
        match verify_pgp(dest, rel.pgp_url):
            case True:
                ok("PGP signature valid (kernel.org release key)")
                verified = True
            case False:
                dest.unlink(missing_ok=True)
                raise VerifyError(f"PGP verification FAILED for {dest.name}; archive removed")
            case None:
                note("PGP verification unavailable (gpg or keys missing)")
    if not verified:
        exp = expected_sha256(rel.archive_name, rel.version)
        if exp:
            actual = sha256_file(dest)
            if actual != exp:
                dest.unlink(missing_ok=True)
                raise VerifyError(f"SHA256 mismatch for {dest.name}: expected {exp[:16]}..., got {actual[:16]}...; archive removed")
            ok("SHA256 matches kernel.org sha256sums.asc")
            verified = True
    if not verified:
        if rel.is_rc:
            warn("-rc snapshots from git.kernel.org carry no signature or checksum; continuing because allow_rc/pin opted in")
        elif require_signature:
            raise VerifyError(f"Could not verify {dest.name} (no PGP, no SHA256). Set release.require_signature=false to override.")
        else:
            warn(f"{dest.name} is unverified (require_signature=false)")
    return dest


def is_valid_kernel_tree(p: Path) -> bool:
    return (p / "Makefile").is_file() and (p / "Kconfig").is_file() and (p / "kernel" / "Kconfig.hz").is_file()


def tree_version(tree: Path) -> str:
    mf = _read(tree / "Makefile")
    v = re.search(r"^VERSION\s*=\s*(\d+)", mf, re.M)
    pl = re.search(r"^PATCHLEVEL\s*=\s*(\d+)", mf, re.M)
    sl = re.search(r"^SUBLEVEL\s*=\s*(\d+)", mf, re.M)
    extra = re.search(r"^EXTRAVERSION\s*=\s*(\S*)", mf, re.M)
    if not (v and pl):
        return "unknown"
    res = f"{v.group(1)}.{pl.group(1)}"
    if sl and sl.group(1) != "0":
        res += f".{sl.group(1)}"
    if extra and extra.group(1):
        res += extra.group(1)
    return res


def tree_dir_for(rel: Release, patchset: str) -> Path:
    return SRC_DIR / (f"linux-{rel.version}" + (f"+{patchset}" if patchset else ""))


def unpack(tarball: Path, rel: Release, patchset: str, fresh: bool) -> Path:
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    dest = tree_dir_for(rel, patchset)
    if fresh and dest.exists():
        info(f"--fresh: removing {dest}")
        shutil.rmtree(dest)
    if is_valid_kernel_tree(dest):
        ok(f"Reusing source tree {dest.name} (incremental build)")
        return dest
    rule(f"Extracting {tarball.name}")
    with tempfile.TemporaryDirectory(dir=SRC_DIR, prefix=".extract-") as tmp:
        run(["tar", "-xf", str(tarball), "-C", tmp])
        inner = [d for d in Path(tmp).iterdir() if d.is_dir()]
        if len(inner) != 1 or not is_valid_kernel_tree(inner[0]):
            raise BuildError(f"Unexpected archive layout in {tarball.name}")
        if dest.exists():
            shutil.rmtree(dest)
        inner[0].rename(dest)
    kv = KVer.parse(tree_version(dest).split("-dusky")[0])
    if kv is None or kv.key() < KVer(*MIN_KERNEL).key():
        raise ProfileError(f"Extracted tree reports {tree_version(dest)}, below the {MIN_KERNEL[0]}.{MIN_KERNEL[1]} floor")
    ok(f"Extracted Linux {tree_version(dest)} -> {dest}")
    return dest

# ---------------------------------------------------------------------------------------------------
# Out-of-tree scheduler patches (BORE / Project C BMQ)
# ---------------------------------------------------------------------------------------------------
CACHYOS_RAW: Final = "https://raw.githubusercontent.com/CachyOS/kernel-patches/master"


def github_dir_patches(owner_repo: str, path: str) -> list[str]:
    data = json.loads(http_get(f"https://api.github.com/repos/{owner_repo}/contents/{path}").decode("utf-8"))
    if not isinstance(data, list):
        return []
    return sorted(str(e["download_url"]) for e in data if isinstance(e, dict) and str(e.get("name", "")).endswith(".patch") and e.get("download_url"))


def gitlab_dir_patches(project: str, path: str) -> list[str]:
    api = f"https://gitlab.com/api/v4/projects/{urllib.parse.quote_plus(project)}/repository/tree?path={urllib.parse.quote(path)}&per_page=100"
    data = json.loads(http_get(api).decode("utf-8"))
    names = sorted(str(e["name"]) for e in data if isinstance(e, dict) and e.get("type") == "blob" and str(e["name"]).endswith(".patch"))
    return [f"https://gitlab.com/{project}/-/raw/master/{path}/{n}" for n in names]


def resolve_patch_urls(sched: str, mm: str, is_rc: bool, sources: Sequence[str]) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []
    for src in sources:
        match (sched, src):
            case ("bore", "cachyos"):
                urls += [("cachyos", f"{CACHYOS_RAW}/{mm}/sched/0001-bore.patch"), ("cachyos", f"{CACHYOS_RAW}/{mm}/sched/0001-bore-cachy.patch")]
            case ("bore", "upstream_author"):
                subdirs = [f"patches/stable/linux-{mm}-bore", f"patches/testing/linux-{mm}-rc-bore"]
                if is_rc:
                    subdirs.reverse()
                for sub in subdirs:
                    try:
                        urls += [("firelzrd", u) for u in github_dir_patches("firelzrd/bore-scheduler", sub)]
                    except (NetworkError, ValueError, KeyError):
                        continue
            case ("bore", "tkg"):
                local_tkg = Path(f"/mnt/zram1/linux-tkg-master/linux-tkg-patches/{mm}/0001-bore.patch")
                if local_tkg.is_file():
                    urls.append(("tkg", str(local_tkg)))
                urls.append(("tkg", f"https://raw.githubusercontent.com/Frogging-Family/linux-tkg/master/linux-tkg-patches/{mm}/0001-bore.patch"))
            case ("bmq", "cachyos"):
                urls += [("cachyos", f"{CACHYOS_RAW}/{mm}/sched/0001-prjc.patch"), ("cachyos", f"{CACHYOS_RAW}/{mm}/sched/0001-prjc-cachy.patch")]
            case ("bmq", "upstream_author"):
                try:
                    urls += [("projectc", u) for u in reversed(gitlab_dir_patches("alfredchen/projectc", mm))]
                except (NetworkError, ValueError, KeyError):
                    pass
            case _:
                if src.startswith(("http://", "https://", "/", "file://")):
                    urls.append(("custom", src.replace("{mm}", mm)))
    return urls


def fetch_patch(url: str, sched: str, mm: str) -> Path | None:
    dest = PATCH_CACHE / sched / mm / Path(urllib.parse.urlparse(url).path).name
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("/") or url.startswith("file://"):
        local_p = Path(url.removeprefix("file://"))
        if local_p.is_file():
            data = local_p.read_bytes()
            if b"diff --git" in data or b"\n+++ " in data:
                dest.write_bytes(data)
                return dest
        return None
    try:
        data = http_get(url, timeout=60)
    except NetworkError:
        return None
    if b"diff --git" not in data and b"\n+++ " not in data:
        return None
    dest.write_bytes(data)
    return dest


def _patch_state(tree: Path) -> Json:
    try:
        return json.loads((tree / ".dusky" / "patches.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"applied": []}


def _save_patch_state(tree: Path, state: Json) -> None:
    (tree / ".dusky").mkdir(exist_ok=True)
    (tree / ".dusky" / "patches.json").write_text(json.dumps(state, indent=1), encoding="utf-8")


def apply_scheduler_patch(tree: Path, p: KernelProfile, rel: Release) -> str:
    """Returns the effective base scheduler after patching ('eevdf' | 'bore' | 'bmq')."""
    sched = p.g("scheduler", "type")
    if sched == "eevdf":
        return "eevdf"
    state = _patch_state(tree)
    if sched in state.get("applied", []):
        ok(f"{sched.upper()} patch already applied to {tree.name}")
        return sched
    rule(f"Out-of-tree scheduler patch: {sched.upper()}")
    kv = rel.kver
    mm = f"{kv.major}.{kv.minor}"
    for origin, url in resolve_patch_urls(sched, mm, rel.is_rc, p.g("scheduler", "patch_sources")):
        pf = fetch_patch(url, sched, mm)
        if pf is None:
            debug(f"no patch at {url}")
            continue
        dry = run(["patch", "-p1", "-N", "--dry-run", "-F0", "-i", str(pf)], cwd=tree, check=False)
        if dry.returncode != 0:
            dry = run(["patch", "-p1", "-N", "--dry-run", "-i", str(pf)], cwd=tree, check=False)
            if dry.returncode != 0:
                warn(f"{origin}: {pf.name} does not apply cleanly to Linux {mm}; trying the next source")
                continue
            warn(f"{origin}: {pf.name} applies with fuzz")
        run(["patch", "-p1", "-N", "-i", str(pf)], cwd=tree)
        state.setdefault("applied", []).append(sched)
        state[sched] = {"url": url, "file": pf.name, "applied_at": datetime.now(UTC).isoformat()}
        _save_patch_state(tree, state)
        ok(f"Applied {origin} {pf.name}")
        return sched
    if p.g("scheduler", "require_patch"):
        raise BuildError(f"No applicable {sched.upper()} patch found for Linux {mm} (require_patch=true)")
    if p.g("scheduler", "allow_vanilla_fallback"):
        warn(f"No applicable {sched.upper()} patch for Linux {mm}; falling back to vanilla EEVDF")
        p.set("scheduler", "type", "eevdf", explicit=False)
        return "eevdf"
    raise BuildError(f"No applicable {sched.upper()} patch for Linux {mm} and vanilla fallback is disabled")


# ---------------------------------------------------------------------------------------------------
# Built-in kernel enhancement patches (Linux 7.2 / 7.3+)
# ---------------------------------------------------------------------------------------------------
BUILTIN_PATCH_SCHED_INLINE: Final = r"""diff --git a/arch/arm/include/asm/mmu_context.h b/arch/arm/include/asm/mmu_context.h
index db2cb06aa..bebde469f 100644
--- a/arch/arm/include/asm/mmu_context.h
+++ b/arch/arm/include/asm/mmu_context.h
@@ -80,7 +80,7 @@ static inline void check_and_switch_context(struct mm_struct *mm,
 #ifndef MODULE
 #define finish_arch_post_lock_switch \
 	finish_arch_post_lock_switch
-static inline void finish_arch_post_lock_switch(void)
+static __always_inline void finish_arch_post_lock_switch(void)
 {
 	struct mm_struct *mm = current->mm;
 
diff --git a/arch/riscv/include/asm/sync_core.h b/arch/riscv/include/asm/sync_core.h
index 9153016da..2fe6b7fe6 100644
--- a/arch/riscv/include/asm/sync_core.h
+++ b/arch/riscv/include/asm/sync_core.h
@@ -6,7 +6,7 @@
  * RISC-V implements return to user-space through an xRET instruction,
  * which is not core serializing.
  */
-static inline void sync_core_before_usermode(void)
+static __always_inline void sync_core_before_usermode(void)
 {
 	asm volatile ("fence.i" ::: "memory");
 }
diff --git a/arch/s390/include/asm/mmu_context.h b/arch/s390/include/asm/mmu_context.h
index bd1ef5e2d..95d03be2c 100644
--- a/arch/s390/include/asm/mmu_context.h
+++ b/arch/s390/include/asm/mmu_context.h
@@ -93,7 +93,7 @@ static inline void switch_mm(struct mm_struct *prev, struct mm_struct *next,
 }
 
 #define finish_arch_post_lock_switch finish_arch_post_lock_switch
-static inline void finish_arch_post_lock_switch(void)
+static __always_inline void finish_arch_post_lock_switch(void)
 {
 	struct task_struct *tsk = current;
 	struct mm_struct *mm = tsk->mm;
diff --git a/arch/sparc/include/asm/mmu_context_64.h b/arch/sparc/include/asm/mmu_context_64.h
index 78bbacc14..d1967214e 100644
--- a/arch/sparc/include/asm/mmu_context_64.h
+++ b/arch/sparc/include/asm/mmu_context_64.h
@@ -160,7 +160,7 @@ static inline void arch_start_context_switch(struct task_struct *prev)
 }
 
 #define finish_arch_post_lock_switch	finish_arch_post_lock_switch
-static inline void finish_arch_post_lock_switch(void)
+static __always_inline void finish_arch_post_lock_switch(void)
 {
 	/* Restore the state of MCDPER register for the new process
 	 * just switched to.
diff --git a/arch/x86/include/asm/sync_core.h b/arch/x86/include/asm/sync_core.h
index 96bda4353..4b55fa353 100644
--- a/arch/x86/include/asm/sync_core.h
+++ b/arch/x86/include/asm/sync_core.h
@@ -93,7 +93,7 @@ static __always_inline void sync_core(void)
  * to user-mode. x86 implements return to user-space through sysexit,
  * sysrel, and sysretq, which are not core serializing.
  */
-static inline void sync_core_before_usermode(void)
+static __always_inline void sync_core_before_usermode(void)
 {
 	/* With PTI, we unconditionally serialize before running user code. */
 	if (static_cpu_has(X86_FEATURE_PTI))
diff --git a/include/linux/perf_event.h b/include/linux/perf_event.h
index 48d851fbd..7c1dac8da 100644
--- a/include/linux/perf_event.h
+++ b/include/linux/perf_event.h
@@ -1632,7 +1632,7 @@ static inline void perf_event_task_migrate(struct task_struct *task)
 		task->sched_migrated = 1;
 }
 
-static inline void perf_event_task_sched_in(struct task_struct *prev,
+static __always_inline void perf_event_task_sched_in(struct task_struct *prev,
 					    struct task_struct *task)
 {
 	if (static_branch_unlikely(&perf_sched_events))
diff --git a/include/linux/sched/mm.h b/include/linux/sched/mm.h
index 95d0040df..4a279ee2d 100644
--- a/include/linux/sched/mm.h
+++ b/include/linux/sched/mm.h
@@ -44,7 +44,7 @@ static inline void smp_mb__after_mmgrab(void)
 
 extern void __mmdrop(struct mm_struct *mm);
 
-static inline void mmdrop(struct mm_struct *mm)
+static __always_inline void mmdrop(struct mm_struct *mm)
 {
 	/*
 	 * The implicit full barrier implied by atomic_dec_and_test() is
@@ -71,14 +71,14 @@ static inline void __mmdrop_delayed(struct rcu_head *rhp)
  * Invoked from finish_task_switch(). Delegates the heavy lifting on RT
  * kernels via RCU.
  */
-static inline void mmdrop_sched(struct mm_struct *mm)
+static __always_inline void mmdrop_sched(struct mm_struct *mm)
 {
 	/* Provides a full memory barrier. See mmdrop() */
 	if (atomic_dec_and_test(&mm->mm_count))
 		call_rcu(&mm->delayed_drop, __mmdrop_delayed);
 }
 #else
-static inline void mmdrop_sched(struct mm_struct *mm)
+static __always_inline void mmdrop_sched(struct mm_struct *mm)
 {
 	mmdrop(mm);
 }
@@ -104,7 +104,7 @@ static inline void mmdrop_lazy_tlb(struct mm_struct *mm)
 	}
 }
 
-static inline void mmdrop_lazy_tlb_sched(struct mm_struct *mm)
+static __always_inline void mmdrop_lazy_tlb_sched(struct mm_struct *mm)
 {
 	if (IS_ENABLED(CONFIG_MMU_LAZY_TLB_REFCOUNT))
 		mmdrop_sched(mm);
@@ -532,7 +532,7 @@ enum {
 #include <asm/membarrier.h>
 #endif
 
-static inline void membarrier_mm_sync_core_before_usermode(struct mm_struct *mm)
+static __always_inline void membarrier_mm_sync_core_before_usermode(struct mm_struct *mm)
 {
 	/*
 	 * The atomic_read() below prevents CSE. The following should
diff --git a/include/linux/tick.h b/include/linux/tick.h
index 1cf4651f0..2f91eccd2 100644
--- a/include/linux/tick.h
+++ b/include/linux/tick.h
@@ -173,7 +173,7 @@ extern cpumask_var_t tick_nohz_full_mask;
 #ifdef CONFIG_NO_HZ_FULL
 extern bool tick_nohz_full_running;
 
-static inline bool tick_nohz_full_enabled(void)
+static __always_inline bool tick_nohz_full_enabled(void)
 {
 	if (!context_tracking_enabled())
 		return false;
@@ -297,7 +297,7 @@ static inline void __tick_nohz_task_switch(void) { }
 static inline void tick_nohz_full_setup(cpumask_var_t cpumask) { }
 #endif
 
-static inline void tick_nohz_task_switch(void)
+static __always_inline void tick_nohz_task_switch(void)
 {
 	if (tick_nohz_full_enabled())
 		__tick_nohz_task_switch();
diff --git a/include/linux/vtime.h b/include/linux/vtime.h
index 82825e775..28234dda2 100644
--- a/include/linux/vtime.h
+++ b/include/linux/vtime.h
@@ -26,12 +26,12 @@ static inline void vtime_guest_exit(struct task_struct *tsk) { }
 static inline void vtime_init_idle(struct task_struct *tsk, int cpu) { }
 #endif
 
-static inline bool vtime_generic_enabled_cpu(int cpu)
+static __always_inline bool vtime_generic_enabled_cpu(int cpu)
 {
 	return context_tracking_enabled_cpu(cpu);
 }
 
-static inline bool vtime_generic_enabled_this_cpu(void)
+static __always_inline bool vtime_generic_enabled_this_cpu(void)
 {
 	return context_tracking_enabled_this_cpu();
 }
@@ -89,24 +89,24 @@ static __always_inline void vtime_account_guest_exit(void)
  * For now vtime state is tied to context tracking. We might want to decouple
  * those later if necessary.
  */
-static inline bool vtime_accounting_enabled(void)
+static __always_inline bool vtime_accounting_enabled(void)
 {
 	return context_tracking_enabled();
 }
 
-static inline bool vtime_accounting_enabled_cpu(int cpu)
+static __always_inline bool vtime_accounting_enabled_cpu(int cpu)
 {
 	return vtime_generic_enabled_cpu(cpu);
 }
 
-static inline bool vtime_accounting_enabled_this_cpu(void)
+static __always_inline bool vtime_accounting_enabled_this_cpu(void)
 {
 	return vtime_generic_enabled_this_cpu();
 }
 
 extern void vtime_task_switch_generic(struct task_struct *prev);
 
-static inline void vtime_task_switch(struct task_struct *prev)
+static __always_inline void vtime_task_switch(struct task_struct *prev)
 {
 	if (vtime_accounting_enabled_this_cpu())
 		vtime_task_switch_generic(prev);
diff --git a/kernel/sched/core.c b/kernel/sched/core.c
index 96226707c..6014cc8fb 100644
--- a/kernel/sched/core.c
+++ b/kernel/sched/core.c
@@ -5074,7 +5074,7 @@ static inline void prepare_task(struct task_struct *next)
 	WRITE_ONCE(next->on_cpu, 1);
 }
 
-static inline void finish_task(struct task_struct *prev)
+static __always_inline void finish_task(struct task_struct *prev)
 {
 	/*
 	 * This must be the very last reference to @prev from this CPU. After
@@ -5118,7 +5118,7 @@ static void zap_balance_callbacks(struct rq *rq)
 	rq->balance_callback = found ? &balance_push_callback : NULL;
 }
 
-static void do_balance_callbacks(struct rq *rq, struct balance_callback *head)
+static __always_inline void do_balance_callbacks(struct rq *rq, struct balance_callback *head)
 {
 	void (*func)(struct rq *rq);
 	struct balance_callback *next;
@@ -5153,7 +5153,7 @@ struct balance_callback balance_push_callback = {
 	.func = balance_push,
 };
 
-static inline struct balance_callback *
+static __always_inline struct balance_callback *
 __splice_balance_callbacks(struct rq *rq, bool split)
 {
 	struct balance_callback *head = rq->balance_callback;
@@ -5183,7 +5183,7 @@ struct balance_callback *splice_balance_callbacks(struct rq *rq)
 	return __splice_balance_callbacks(rq, true);
 }
 
-void __balance_callbacks(struct rq *rq, struct rq_flags *rf)
+__always_inline void __balance_callbacks(struct rq *rq, struct rq_flags *rf)
 {
 	if (rf)
 		rq_unpin_lock(rq, rf);
@@ -5227,7 +5227,7 @@ prepare_lock_switch(struct rq *rq, struct task_struct *next, struct rq_flags *rf
 	__acquire(__rq_lockp(this_rq()));
 }
 
-static inline void finish_lock_switch(struct rq *rq)
+static __always_inline void finish_lock_switch(struct rq *rq)
 	__releases(__rq_lockp(rq))
 {
 	/*
@@ -5261,7 +5261,7 @@ static inline void kmap_local_sched_out(void)
 #endif
 }
 
-static inline void kmap_local_sched_in(void)
+static __always_inline void kmap_local_sched_in(void)
 {
 #ifdef CONFIG_KMAP_LOCAL
 	if (unlikely(current->kmap_ctrl.idx))
@@ -5315,7 +5315,7 @@ prepare_task_switch(struct rq *rq, struct task_struct *prev,
  * past. 'prev == current' is still correct but we need to recalculate this_rq
  * because prev may have moved to another CPU.
  */
-static struct rq *finish_task_switch(struct task_struct *prev)
+static __always_inline struct rq *finish_task_switch(struct task_struct *prev)
 	__releases(__rq_lockp(this_rq()))
 {
 	struct rq *rq = this_rq();
diff --git a/kernel/sched/sched.h b/kernel/sched/sched.h
index 56acf502b..b8e75cdc2 100644
--- a/kernel/sched/sched.h
+++ b/kernel/sched/sched.h
@@ -1457,12 +1457,12 @@ static inline struct cpumask *sched_group_span(struct sched_group *sg);
 
 DECLARE_STATIC_KEY_FALSE(__sched_core_enabled);
 
-static inline bool sched_core_enabled(struct rq *rq)
+static __always_inline bool sched_core_enabled(struct rq *rq)
 {
 	return static_branch_unlikely(&__sched_core_enabled) && rq->core_enabled;
 }
 
-static inline bool sched_core_disabled(void)
+static __always_inline bool sched_core_disabled(void)
 {
 	return !static_branch_unlikely(&__sched_core_enabled);
 }
@@ -1471,7 +1471,7 @@ static inline bool sched_core_disabled(void)
  * Be careful with this function; not for general use. The return value isn't
  * stable unless you actually hold a relevant rq->__lock.
  */
-static inline raw_spinlock_t *rq_lockp(struct rq *rq)
+static __always_inline raw_spinlock_t *rq_lockp(struct rq *rq)
 {
 	if (sched_core_enabled(rq))
 		return &rq->core->__lock;
@@ -1479,7 +1479,7 @@ static inline raw_spinlock_t *rq_lockp(struct rq *rq)
 	return &rq->__lock;
 }
 
-static inline raw_spinlock_t *__rq_lockp(struct rq *rq)
+static __always_inline raw_spinlock_t *__rq_lockp(struct rq *rq)
 	__returns_ctx_lock(rq_lockp(rq)) /* alias them */
 {
 	if (rq->core_enabled)
@@ -1582,12 +1582,12 @@ static inline bool sched_core_disabled(void)
 	return true;
 }
 
-static inline raw_spinlock_t *rq_lockp(struct rq *rq)
+static __always_inline raw_spinlock_t *rq_lockp(struct rq *rq)
 {
 	return &rq->__lock;
 }
 
-static inline raw_spinlock_t *__rq_lockp(struct rq *rq)
+static __always_inline raw_spinlock_t *__rq_lockp(struct rq *rq)
 	__returns_ctx_lock(rq_lockp(rq)) /* alias them */
 {
 	return &rq->__lock;
@@ -1647,26 +1647,26 @@ extern void raw_spin_rq_lock_nested(struct rq *rq, int subclass)
 extern bool raw_spin_rq_trylock(struct rq *rq)
 	__cond_acquires(true, __rq_lockp(rq));
 
-static inline void raw_spin_rq_lock(struct rq *rq)
+static __always_inline void raw_spin_rq_lock(struct rq *rq)
 	__acquires(__rq_lockp(rq))
 {
 	raw_spin_rq_lock_nested(rq, 0);
 }
 
-static inline void raw_spin_rq_unlock(struct rq *rq)
+static __always_inline void raw_spin_rq_unlock(struct rq *rq)
 	__releases(__rq_lockp(rq))
 {
 	raw_spin_unlock(rq_lockp(rq));
 }
 
-static inline void raw_spin_rq_lock_irq(struct rq *rq)
+static __always_inline void raw_spin_rq_lock_irq(struct rq *rq)
 	__acquires(__rq_lockp(rq))
 {
 	local_irq_disable();
 	raw_spin_rq_lock(rq);
 }
 
-static inline void raw_spin_rq_unlock_irq(struct rq *rq)
+static __always_inline void raw_spin_rq_unlock_irq(struct rq *rq)
 	__releases(__rq_lockp(rq))
 {
 	raw_spin_rq_unlock(rq);
"""

BUILTIN_PATCH_EVDEV_RCU: Final = r"""diff --git a/drivers/input/evdev.c b/drivers/input/evdev.c
index c7325226c..4f18a50ea 100644
--- a/drivers/input/evdev.c
+++ b/drivers/input/evdev.c
@@ -46,6 +46,7 @@ struct evdev_client {
 	struct fasync_struct *fasync;
 	struct evdev *evdev;
 	struct list_head node;
+	struct rcu_head rcu;
 	enum input_clock_type clk_type;
 	bool revoked;
 	unsigned long *evmasks[EV_CNT];
@@ -368,13 +369,22 @@ static void evdev_attach_client(struct evdev *evdev,
 	spin_unlock(&evdev->client_lock);
 }
 
+static void evdev_reclaim_client(struct rcu_head *rp)
+{
+	struct evdev_client *client = container_of(rp, struct evdev_client, rcu);
+	unsigned int i;
+	for (i = 0; i < EV_CNT; ++i)
+		bitmap_free(client->evmasks[i]);
+	kvfree(client);
+}
+
 static void evdev_detach_client(struct evdev *evdev,
 				struct evdev_client *client)
 {
 	spin_lock(&evdev->client_lock);
 	list_del_rcu(&client->node);
 	spin_unlock(&evdev->client_lock);
-	synchronize_rcu();
+	call_rcu(&client->rcu, evdev_reclaim_client);
 }
 
 static int evdev_open_device(struct evdev *evdev)
@@ -427,7 +437,6 @@ static int evdev_release(struct inode *inode, struct file *file)
 {
 	struct evdev_client *client = file->private_data;
 	struct evdev *evdev = client->evdev;
-	unsigned int i;
 
 	mutex_lock(&evdev->mutex);
 
@@ -439,11 +448,6 @@ static int evdev_release(struct inode *inode, struct file *file)
 
 	evdev_detach_client(evdev, client);
 
-	for (i = 0; i < EV_CNT; ++i)
-		bitmap_free(client->evmasks[i]);
-
-	kvfree(client);
-
 	evdev_close_device(evdev);
 
 	return 0;
@@ -486,7 +490,6 @@ static int evdev_open(struct inode *inode, struct file *file)
 
  err_free_client:
 	evdev_detach_client(evdev, client);
-	kvfree(client);
 	return error;
 }
 
"""

BUILTIN_PATCH_PCI_PME: Final = r"""diff --git a/drivers/pci/pci.c b/drivers/pci/pci.c
--- a/drivers/pci/pci.c
+++ b/drivers/pci/pci.c
@@ -62,7 +62,7 @@ struct pci_pme_device {
 	struct pci_dev *dev;
 };
 
-#define PME_TIMEOUT 1000 /* How long between PME checks */
+#define PME_TIMEOUT 4000 /* How long between PME checks */
 
 static void pci_dev_d3_sleep(struct pci_dev *dev)
 {
"""

BUILTIN_PATCH_CLANG_POLLY: Final = r"""diff --git a/Makefile b/Makefile
--- a/Makefile
+++ b/Makefile
@@ -870,6 +870,23 @@ endif
 KBUILD_RUSTFLAGS += -Cdebug-assertions=$(if $(CONFIG_RUST_DEBUG_ASSERTIONS),y,n)
 KBUILD_RUSTFLAGS += -Coverflow-checks=$(if $(CONFIG_RUST_OVERFLOW_CHECKS),y,n)
 
+ifdef CONFIG_POLLY_CLANG
+KBUILD_CFLAGS	+= -fplugin=LLVMPolly.so \
+		   -mllvm -polly \
+		   -mllvm -polly-ast-use-context \
+		   -mllvm -polly-invariant-load-hoisting \
+		   -mllvm -polly-loopfusion-greedy \
+		   -mllvm -polly-run-inliner \
+		   -mllvm -polly-vectorizer=stripmine
+# Polly may optimise loops with dead paths beyound what the linker
+# can understand. This may negate the effect of the linker's DCE
+# so we tell Polly to perfom proven DCE on the loops it optimises
+# in order to preserve the overall effect of the linker's DCE.
+ifdef CONFIG_LD_DEAD_CODE_DATA_ELIMINATION
+KBUILD_CFLAGS	+= -mllvm -polly-run-dce
+endif
+endif
+
 # Tell gcc to never replace conditional load with a non-conditional one
 ifdef CONFIG_CC_IS_GCC
 # gcc-10 renamed --param=allow-store-data-races=0 to
diff --git a/init/Kconfig b/init/Kconfig
--- a/init/Kconfig
+++ b/init/Kconfig
@@ -251,6 +251,19 @@ config BUILD_SALT
 	  This is mostly useful for distributions which want to ensure the
 	  build is unique between builds. It's safe to leave the default.
 
+config POLLY_CLANG
+	bool "Use Clang Polly optimizations"
+	depends on CC_IS_CLANG && $(cc-option,-mllvm -polly -fplugin=LLVMPolly.so)
+	depends on !COMPILE_TEST
+	help
+	  This option enables Clang's polyhedral loop optimizer known as
+	  Polly. Polly is able to optimize various loops throughout the
+	  kernel for cache locality. This requires a Clang toolchain
+	  compiled with support for Polly. More information can be found
+	  from Polly's website:
+
+	    https://polly.llvm.org
+
 config HAVE_KERNEL_GZIP
 	bool
 """

BUILTIN_PATCH_OPTIMIZE_O3: Final = r"""diff --git a/Makefile b/Makefile
--- a/Makefile
+++ b/Makefile
@@ -929,16 +929,25 @@ KBUILD_CFLAGS	+= -fno-delete-null-pointer-checks
 ifdef CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE
 KBUILD_CFLAGS += -O2
 KBUILD_RUSTFLAGS += -Copt-level=2
+else ifdef CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE_O3
+KBUILD_CFLAGS += -O3
+KBUILD_RUSTFLAGS += -Copt-level=3
 else ifdef CONFIG_CC_OPTIMIZE_FOR_SIZE
 KBUILD_CFLAGS += -Os
 KBUILD_RUSTFLAGS += -Copt-level=s
 endif
 
+# Perform swing modulo scheduling immediately before the first scheduling pass.
+# This pass looks at innermost loops and reorders their instructions by
+# overlapping different iterations.
+KBUILD_CFLAGS += $(call cc-option,-fmodulo-sched -fmodulo-sched-allow-regmoves -fivopts)
+KBUILD_CFLAGS += $(call cc-option,-mllvm -enable-pipeliner)
+
 # Always set `debug-assertions` and `overflow-checks` because their default
 # depends on `opt-level` and `debug-assertions`, respectively.
 KBUILD_RUSTFLAGS += -Cdebug-assertions=$(if $(CONFIG_RUST_DEBUG_ASSERTIONS),y,n)
 KBUILD_RUSTFLAGS += -Coverflow-checks=$(if $(CONFIG_RUST_OVERFLOW_CHECKS),y,n)
 
 # Tell gcc to never replace conditional load with a non-conditional one
 ifdef CONFIG_CC_IS_GCC
 # gcc-10 renamed --param=allow-store-data-races=0 to
@@ -1154,11 +1179,6 @@ KBUILD_CFLAGS	+= -fno-strict-overflow
 # Make sure -fstack-check isn't enabled (like gentoo apparently did)
 KBUILD_CFLAGS  += -fno-stack-check
 
-# conserve stack if available
-ifdef CONFIG_CC_IS_GCC
-KBUILD_CFLAGS   += -fconserve-stack
-endif
-
 # Ensure compilers do not transform certain loops into calls to wcslen()
 KBUILD_CFLAGS += -fno-builtin-wcslen
 
 diff --git a/arch/x86/Makefile b/arch/x86/Makefile
--- a/arch/x86/Makefile
+++ b/arch/x86/Makefile
@@ -73,7 +73,7 @@ export BITS
 #
 #    https://gcc.gnu.org/bugzilla/show_bug.cgi?id=53383
 #
-KBUILD_CFLAGS += -mno-sse -mno-mmx -mno-sse2 -mno-3dnow -mno-avx -mno-sse4a
+KBUILD_CFLAGS += -mno-sse -mno-mmx -mno-sse2 -mno-3dnow -mno-avx -mno-sse4a -mno-avx2 -fno-tree-vectorize
 KBUILD_RUSTFLAGS += --target=$(objtree)/scripts/target.json
 KBUILD_RUSTFLAGS += -Ctarget-feature=-sse,-sse2,-sse3,-ssse3,-sse4.1,-sse4.2,-avx,-avx2
 
diff --git a/init/Kconfig b/init/Kconfig
--- a/init/Kconfig
+++ b/init/Kconfig
@@ -1613,6 +1613,12 @@ config CC_OPTIMIZE_FOR_PERFORMANCE
 	  with the "-O2" compiler flag for best performance and most
 	  helpful compile-time warnings.
 
+config CC_OPTIMIZE_FOR_PERFORMANCE_O3
+	bool "Optimize more for performance (-O3)"
+	help
+	  Choosing this option will pass "-O3" to your compiler to optimize
+	  the kernel yet more for performance.
+
 config CC_OPTIMIZE_FOR_SIZE
 	bool "Optimize for size (-Os)"
 	help
"""

BUILTIN_PATCH_ACS_OVERRIDE: Final = r"""diff --git a/drivers/pci/quirks.c b/drivers/pci/quirks.c
index 4700d24e5d55..8f7a3d7fd9c1 100644
--- a/drivers/pci/quirks.c
+++ b/drivers/pci/quirks.c
@@ -3372,6 +3372,106 @@ static void quirk_no_bus_reset(struct pci_dev *dev)
 	dev->dev_flags |= PCI_DEV_FLAGS_NO_BUS_RESET;
 }
 
+static bool acs_on_downstream;
+static bool acs_on_multifunction;
+
+#define NUM_ACS_IDS 16
+struct acs_on_id {
+	unsigned short vendor;
+	unsigned short device;
+};
+static struct acs_on_id acs_on_ids[NUM_ACS_IDS];
+static u8 max_acs_id;
+
+static __init int pcie_acs_override_setup(char *p)
+{
+	if (!p)
+		return -EINVAL;
+
+	while (*p) {
+		if (!strncmp(p, "downstream", 10))
+			acs_on_downstream = true;
+		if (!strncmp(p, "multifunction", 13))
+			acs_on_multifunction = true;
+		if (!strncmp(p, "id:", 3)) {
+			char opt[5];
+			int ret;
+			long val;
+
+			if (max_acs_id >= NUM_ACS_IDS - 1) {
+				pr_warn("Out of PCIe ACS override slots (%d)\n",
+						NUM_ACS_IDS);
+				goto next;
+			}
+
+			p += 3;
+			snprintf(opt, 5, "%s", p);
+			ret = kstrtol(opt, 16, &val);
+			if (ret) {
+				pr_warn("PCIe ACS ID parse error %d\n", ret);
+				goto next;
+			}
+			acs_on_ids[max_acs_id].vendor = val;
+
+			p += strcspn(p, ":");
+			if (*p != ':') {
+				pr_warn("PCIe ACS invalid ID\n");
+				goto next;
+			}
+
+			p++;
+			snprintf(opt, 5, "%s", p);
+			ret = kstrtol(opt, 16, &val);
+			if (ret) {
+				pr_warn("PCIe ACS ID parse error %d\n", ret);
+				goto next;
+			}
+			acs_on_ids[max_acs_id].device = val;
+			max_acs_id++;
+		}
+next:
+		p += strcspn(p, ",");
+		if (*p == ',')
+			p++;
+	}
+
+	if (acs_on_downstream || acs_on_multifunction || max_acs_id)
+		pr_warn("Warning: PCIe ACS overrides enabled; This may allow non-IOMMU protected peer-to-peer DMA\n");
+
+	return 0;
+}
+early_param("pcie_acs_override", pcie_acs_override_setup);
+
+static int pcie_acs_overrides(struct pci_dev *dev, u16 acs_flags)
+{
+	int i;
+
+	/* Never override ACS for legacy devices or devices with ACS caps */
+	if (!pci_is_pcie(dev) ||
+		pci_find_ext_capability(dev, PCI_EXT_CAP_ID_ACS))
+			return -ENOTTY;
+
+	for (i = 0; i < max_acs_id; i++)
+		if (acs_on_ids[i].vendor == dev->vendor &&
+			acs_on_ids[i].device == dev->device)
+				return 1;
+
+	switch (pci_pcie_type(dev)) {
+	case PCI_EXP_TYPE_DOWNSTREAM:
+	case PCI_EXP_TYPE_ROOT_PORT:
+		if (acs_on_downstream)
+			return 1;
+		break;
+	case PCI_EXP_TYPE_ENDPOINT:
+	case PCI_EXP_TYPE_UPSTREAM:
+	case PCI_EXP_TYPE_LEG_END:
+	case PCI_EXP_TYPE_RC_END:
+		if (acs_on_multifunction && dev->multifunction)
+			return 1;
+	}
+
+	return -ENOTTY;
+}
 /*
  * Some Atheros AR9xxx and QCA988x chips do not behave after a bus reset.
  * The device will throw a Link Down error on AER-capable systems and
@@ -5102,6 +5102,7 @@
 	{ PCI_VENDOR_ID_ZHAOXIN, PCI_ANY_ID, pci_quirk_zhaoxin_pcie_ports_acs },
 	/* Wangxun nics */
 	{ PCI_VENDOR_ID_WANGXUN, PCI_ANY_ID, pci_quirk_wangxun_nic_acs },
+	{ PCI_ANY_ID, PCI_ANY_ID, pcie_acs_overrides },
 	{ 0 }
 };
 

"""


def apply_patch_content(tree: Path, name: str, patch_text: str) -> bool:
    """Applies an embedded patch to the kernel source tree, with idempotency and reverse-dry-run checks."""
    state = _patch_state(tree)
    if name in state.get("applied", []):
        ok(f"Patch '{name}' already applied to {tree.name}")
        return True

    dusky_dir = tree / ".dusky"
    dusky_dir.mkdir(parents=True, exist_ok=True)
    pfile = dusky_dir / f"{name}.patch"
    pfile.write_text(patch_text, encoding="utf-8")

    dry = run(["patch", "-p1", "-N", "--dry-run", "-F0", "-i", str(pfile)], cwd=tree, check=False)
    fuzz = False
    if dry.returncode != 0:
        dry = run(["patch", "-p1", "-N", "--dry-run", "-i", str(pfile)], cwd=tree, check=False)
        if dry.returncode != 0:
            rev_dry = run(["patch", "-p1", "-R", "--dry-run", "-i", str(pfile)], cwd=tree, check=False)
            if rev_dry.returncode == 0:
                state.setdefault("applied", []).append(name)
                state[name] = {"builtin": True, "applied_at": datetime.now(UTC).isoformat(), "note": "already present in tree"}
                _save_patch_state(tree, state)
                ok(f"Patch '{name}' already present in source tree")
                return True
            warn(f"Patch '{name}' does not apply cleanly to Linux {tree.name}; skipping")
            return False
        fuzz = True

    if fuzz:
        warn(f"Patch '{name}' applies with fuzz")
    run(["patch", "-p1", "-N", "-i", str(pfile)], cwd=tree)
    state.setdefault("applied", []).append(name)
    state[name] = {"builtin": True, "applied_at": datetime.now(UTC).isoformat()}
    _save_patch_state(tree, state)
    ok(f"Applied built-in patch: {name}")
    return True


def apply_enhancement_patches(tree: Path, p: KernelProfile, rel: Release, facts: HostFacts) -> None:
    """Applies high-value performance, responsiveness, and safety enhancement patches."""
    rule("Kernel enhancement patches")
    applied_count = 0

    if p.g("dusky", "patch_sched_inline"):
        if apply_patch_content(tree, "sched_inline", BUILTIN_PATCH_SCHED_INLINE):
            applied_count += 1

    if p.g("dusky", "patch_evdev_rcu"):
        if apply_patch_content(tree, "evdev_rcu", BUILTIN_PATCH_EVDEV_RCU):
            applied_count += 1

    if p.g("dusky", "patch_pci_pme"):
        if apply_patch_content(tree, "pci_pme", BUILTIN_PATCH_PCI_PME):
            applied_count += 1

    if p.g("compiler", "polly") and p.g("compiler", "toolchain") == "llvm":
        if apply_patch_content(tree, "clang_polly", BUILTIN_PATCH_CLANG_POLLY):
            applied_count += 1

    if p.g("compiler", "optimize") == "o3":
        if apply_patch_content(tree, "optimize_o3", BUILTIN_PATCH_OPTIMIZE_O3):
            applied_count += 1

    if p.g("boot", "acs_override"):
        if apply_patch_content(tree, "acs_override", BUILTIN_PATCH_ACS_OVERRIDE):
            applied_count += 1

    if applied_count == 0:
        info("No additional enhancement patches selected for this profile")
    else:
        ok(f"Applied/verified {applied_count} enhancement patch(es)")


def inject_dkms_march_in_makefile(tree: Path, march: str, mtune: str) -> bool:
    """Injects microarchitecture flags into the top-level Makefile so out-of-tree and DKMS
    modules built against this kernel/headers tree inherit target CPU optimizations."""
    if not march and not mtune:
        return False
    makefile = tree / "Makefile"
    if not makefile.is_file():
        return False
    txt = makefile.read_text(encoding="utf-8")
    marker = "# Injected by Dusky: propagate target CPU architecture to DKMS/modules"
    if marker in txt:
        return False

    cflags: list[str] = []
    if march:
        cflags.append(f"-march={march}")
    if mtune:
        cflags.append(f"-mtune={mtune}")
    flags_str = " ".join(cflags)

    injection = (
        f"\n{marker}\n"
        f"KBUILD_CFLAGS   += {flags_str}\n"
        f"KBUILD_CPPFLAGS += {flags_str}\n"
    )
    if march:
        injection += f"KBUILD_RUSTFLAGS += -Ctarget-cpu={march}\n"
    injection += "\n"

    target_pattern = r"(KBUILD_CPPFLAGS\s*\+=\s*\$\(KCPPFLAGS\))"
    if re.search(target_pattern, txt):
        new_txt = re.sub(target_pattern, injection + r"\1", txt, count=1)
    else:
        target_pattern2 = r"(KBUILD_CFLAGS\s*\+=\s*\$\(KCFLAGS\))"
        if re.search(target_pattern2, txt):
            new_txt = re.sub(target_pattern2, injection + r"\1", txt, count=1)
        else:
            warn("Could not find KCPPFLAGS/KCFLAGS anchor in Makefile; skipping DKMS march injection")
            return False

    makefile.write_text(new_txt, encoding="utf-8")
    ok(f"Injected DKMS microarchitecture flags ({flags_str}) into top-level Makefile")
    return True


def ensure_hz_choice(tree: Path, hz: int) -> bool:
    """Vanilla trees only offer 100/250/300/1000 Hz. Add a Kconfig choice member for other values (idempotent)."""
    if hz in HZ_UPSTREAM:
        return False
    kfile = tree / "kernel" / "Kconfig.hz"
    txt = kfile.read_text(encoding="utf-8")
    if re.search(rf"^\s*config HZ_{hz}\s*$", txt, re.M):
        return False
    m = re.search(r"^(\s*)config HZ_1000\s*$", txt, re.M)
    if m is None:
        raise BuildError("kernel/Kconfig.hz layout unrecognized; cannot inject HZ choice")
    ind = m.group(1)
    entry = (f"{ind}config HZ_{hz}\n{ind}\tbool \"{hz} HZ\"\n{ind}\thelp\n{ind}\t {hz} Hz timer frequency injected by {APP_NAME}: a middle ground between\n"
             f"{ind}\t 250 Hz throughput and 1000 Hz desktop latency.\n\n")
    txt = txt[:m.start()] + entry + txt[m.start():]
    dm = re.search(r"^(\s*)default 1000 if HZ_1000\s*$", txt, re.M)
    if dm is None:
        raise BuildError("kernel/Kconfig.hz default table unrecognized; cannot inject HZ choice")
    txt = txt[:dm.start()] + f"{dm.group(1)}default {hz} if HZ_{hz}\n" + txt[dm.start():]
    kfile.write_text(txt, encoding="utf-8")
    ok(f"Injected HZ_{hz} into kernel/Kconfig.hz")
    return True


# ---------------------------------------------------------------------------------------------------
# .config seeding, modprobed-db and localmodconfig pruning
# ---------------------------------------------------------------------------------------------------
def is_plausible_kernel_config(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 20000:
        return False
    head = _read(path)
    return "CONFIG_X86_64=y" in head and "CONFIG_MODULES=y" in head


def snapshot_path(p: KernelProfile) -> Path:
    return CONFIG_SNAPSHOT_DIR / f"{p.name}.config"


def arch_upstream_config() -> Path | None:
    dest = BUILD_DIR / "seeds" / "arch-linux.config"
    if dest.is_file() and time.time() - dest.stat().st_mtime < 7 * 86400 and is_plausible_kernel_config(dest):
        return dest
    try:
        data = http_get(ARCH_UPSTREAM_CONFIG_URL, timeout=60)
    except NetworkError as e:
        debug(f"arch config fetch failed: {e}")
        return dest if is_plausible_kernel_config(dest) else None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest if is_plausible_kernel_config(dest) else None


def seed_config(tree: Path, p: KernelProfile, env: Mapping[str, str], override: Path | None) -> str:
    rule("Seed .config")
    dest = tree / ".config"
    if override is not None:
        if not is_plausible_kernel_config(override):
            raise ProfileError(f"--seed-config {override} is not a plausible x86-64 kernel config")
        override.copy(dest)
        ok(f"Seeded from {override}")
        return str(override)
    order = {"auto": ("snapshot", "arch", "running", "headers", "defconfig")}.get(p.g("dusky", "seed"), (p.g("dusky", "seed"),))
    for src in order:
        match src:
            case "snapshot":
                snap = snapshot_path(p)
                if is_plausible_kernel_config(snap):
                    snap.copy(dest)
                    ok(f"Seeded from snapshot {snap}")
                    return "snapshot"
            case "arch":
                cfg = arch_upstream_config()
                if cfg is not None:
                    cfg.copy(dest)
                    ok("Seeded from Arch Linux packaging config (gitlab.archlinux.org)")
                    return "arch"
            case "running":
                gz = Path("/proc/config.gz")
                if gz.is_file():
                    import gzip
                    with gzip.open(gz, "rb") as fh:
                        dest.write_bytes(fh.read())
                    if is_plausible_kernel_config(dest):
                        ok(f"Seeded from /proc/config.gz ({os.uname().release})")
                        return "running"
            case "headers":
                hdr = Path(f"/usr/lib/modules/{os.uname().release}/build/.config")
                if is_plausible_kernel_config(hdr):
                    hdr.copy(dest)
                    ok(f"Seeded from {hdr}")
                    return "headers"
            case "defconfig":
                run(["make", "defconfig"], cwd=tree, env=env)
                warn("Seeded from 'make defconfig' -- not desktop-complete; review the verification report carefully")
                return "defconfig"
    raise BuildError(f"No usable seed found for dusky.seed={p.g('dusky', 'seed')}")


def ensure_modprobed_db(p: KernelProfile) -> Path | None:
    if not p.g("modules", "modprobed_db"):
        return None
    custom = p.g("modules", "modprobed_db_path")
    db = Path(custom).expanduser().resolve() if custom else MODPROBED_DB_PATH
    if not custom and have("modprobed-db"):
        run(["modprobed-db", "store"], check=False, timeout=60)
    if db.is_file() and db.stat().st_size > 0:
        count = len([line for line in _read(db).splitlines() if line.strip()])
        ok(f"modprobed.db: {db} ({count} modules)")
        if count < 40 and not custom:
            warn("modprobed.db is small; use the system for a few days (USB devices, VPN, printers...) before trusting strict mode")
        return db
    if not custom:
        for fallback_db in (Path("/mnt/zram1/linux-tkg-master/linux-tkg-config/7.3/minimal-modprobed.db"),
                            Path("/mnt/zram1/linux-tkg-master/linux-tkg-config/7.2/minimal-modprobed.db")):
            if fallback_db.is_file() and fallback_db.stat().st_size > 0:
                count = len([line for line in _read(fallback_db).splitlines() if line.strip()])
                ok(f"modprobed.db: using bundled fallback {fallback_db.name} ({count} modules)")
                return fallback_db
        warn("modprobed.db missing (install from AUR: paru -S modprobed-db; modprobed-db store)")
    else:
        warn(f"modprobed.db not found at {db}")
    return None


LMC_KEEP_BASE: Final = ("drivers/usb", "drivers/gpu", "drivers/net", "drivers/hid", "drivers/input", "drivers/nvme", "drivers/bluetooth",
                        "drivers/thunderbolt", "drivers/platform/x86", "drivers/media/usb", "sound", "fs", "net/wireless", "crypto")


def localmodconfig(tree: Path, p: KernelProfile, db: Path | None, env: Mapping[str, str]) -> None:
    mode = p.g("modules", "mode")
    rule(f"Module pruning ({mode})")
    lm_env = dict(env)
    if db is not None:
        lm_env["LSMOD"] = str(db)
    elif mode == "strict" and not p.g("modules", "allow_lsmod_fallback"):
        raise ProfileError("strict pruning needs modprobed.db (set modules.allow_lsmod_fallback=true to use the live lsmod set)")
    else:
        warn("Pruning against the live lsmod set only (modules not currently loaded will be dropped)")
    if mode == "expanded":
        lm_env["LMC_KEEP"] = ":".join((*LMC_KEEP_BASE, *p.g("modules", "lmc_keep_extra")))
    target = "localyesconfig" if p.g("modules", "localyesconfig") else "localmodconfig"
    before = sum(1 for line in _read(tree / ".config").splitlines() if line.endswith("=m"))
    run(["make", target], cwd=tree, env=lm_env)
    after = sum(1 for line in _read(tree / ".config").splitlines() if line.endswith("=m"))
    ok(f"{target}: modules {before} -> {after}")


# ---------------------------------------------------------------------------------------------------
# Kconfig symbol index: know exactly which symbols this tree offers (drives skip/soft/hard verification)
# ---------------------------------------------------------------------------------------------------
_KCONFIG_SYM_RE: Final = re.compile(r"^\s*(?:menu)?config\s+([A-Za-z0-9_]+)\s*$", re.M)


@dataclass(slots=True)
class KconfigIndex:
    symbols: frozenset[str]
    x86_64_version_max: int

    @classmethod
    def scan(cls, tree: Path) -> Self:
        syms: set[str] = set()
        skip_dirs = {".git", ".dusky", ".thinlto-cache", "Documentation", "tools", "samples", "LICENSES", "usr", "pacman", "scripts"}
        for root, dirs, files in tree.walk():
            if root == tree / "arch":
                dirs[:] = [d for d in dirs if d == "x86"]
            else:
                dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fn in files:
                if fn == "Kconfig" or fn.startswith("Kconfig."):
                    syms.update(_KCONFIG_SYM_RE.findall(_read(root / fn)))
        vmax = 3
        cpu_kconfig = _read(tree / "arch" / "x86" / "Kconfig.cpu")
        m = re.search(r"config X86_64_VERSION\n(?:.*\n)*?\s*range\s+(\d+)\s+(\d+)", cpu_kconfig)
        if m:
            vmax = int(m.group(2))
        return cls(frozenset(syms), vmax)

    def has(self, sym: str) -> bool:
        """An empty index (no tree scanned yet) is permissive so dry-runs can print the full matrix."""
        return not self.symbols or sym in self.symbols


type OpAction = Literal["y", "n", "m", "val", "str"]


@dataclass(frozen=True, slots=True)
class Op:
    action: OpAction
    symbol: str
    value: int | str | None = None
    optional: bool = False
    why: str = ""

    def render(self) -> str:
        match self.action:
            case "y":
                return f"CONFIG_{self.symbol}=y"
            case "n":
                return f"# CONFIG_{self.symbol} is not set"
            case "m":
                return f"CONFIG_{self.symbol}=m"
            case "val":
                return f"CONFIG_{self.symbol}={self.value}"
            case _:
                return f"CONFIG_{self.symbol}={json.dumps(str(self.value))}"


class Matrix:
    """Ordered, de-duplicated Kconfig operations; symbols absent from the tree are recorded, not applied."""

    def __init__(self, idx: KconfigIndex) -> None:
        self.idx = idx
        self._ops: dict[str, Op] = {}
        self.skipped: list[Op] = []

    def add(self, op: Op) -> None:
        if not self.idx.has(op.symbol):
            self.skipped.append(op)
            return
        self._ops.pop(op.symbol, None)
        self._ops[op.symbol] = op

    def y(self, sym: str, *, optional: bool = False, why: str = "") -> None:
        self.add(Op("y", sym, optional=optional, why=why))

    def n(self, sym: str, *, optional: bool = False, why: str = "") -> None:
        self.add(Op("n", sym, optional=optional, why=why))

    def m(self, sym: str, *, optional: bool = False, why: str = "") -> None:
        self.add(Op("m", sym, optional=optional, why=why))

    def val(self, sym: str, value: int, *, optional: bool = False, why: str = "") -> None:
        self.add(Op("val", sym, value, optional=optional, why=why))

    def s(self, sym: str, value: str, *, optional: bool = False, why: str = "") -> None:
        self.add(Op("str", sym, value, optional=optional, why=why))

    def flag(self, sym: str, on: bool, *, optional: bool = False, why: str = "") -> None:
        (self.y if on else self.n)(sym, optional=optional, why=why)

    def choice(self, members: Iterable[str], selected: str, *, optional: bool = False, why: str = "") -> None:
        for mbr in members:
            if mbr != selected:
                self.n(mbr, optional=True)
        self.y(selected, optional=optional, why=why)

    @property
    def ops(self) -> list[Op]:
        return list(self._ops.values())

    def __len__(self) -> int:
        return len(self._ops)

# ---------------------------------------------------------------------------------------------------
# Derived build state (resolved once, shared by matrix, environment, cmdline and verification)
# ---------------------------------------------------------------------------------------------------
@dataclass(slots=True)
class Derived:
    facts: HostFacts
    idx: KconfigIndex
    tree: Path
    version: str
    sched: str
    toolchain: str
    lto: str
    btf: bool
    tracing: str
    rust: bool
    rust_reason: str
    fdo: str
    fdo_reason: str
    march: str = ""
    mtune: str = ""
    kcflags: list[str] = field(default_factory=list)
    krustflags: list[str] = field(default_factory=list)
    kernelrelease: str = ""
    seed_source: str = ""

    @property
    def scx_class(self) -> bool:
        return self.sched != "bmq"


def derive(p: KernelProfile, facts: HostFacts, idx: KconfigIndex, tree: Path, sched: str, rust_available: bool, rust_output: str) -> Derived:
    s = p.sections
    toolchain = s["compiler"]["toolchain"]
    lto = s["compiler"]["lto"] if toolchain == "llvm" else "none"
    scx_class = bool(s["scheduler"]["scx_enable_class"]) and sched != "bmq"
    tracing = s["memory"]["tracing"]
    if tracing == "auto":
        tracing = "full" if (s["scheduler"]["scx"] != "none" or not p.lean("lean")) else "minimal"
    btf = scx_class or tracing == "full"
    rust, reason = bool(s["compiler"]["rust"]), ""
    if rust and toolchain == "llvm" and not rust_available:
        rust, reason = False, "make LLVM=1 rustavailable failed: " + (rust_output.strip().splitlines() or ["no output"])[-1][:160]
    elif rust and toolchain == "gcc" and not rust_available:
        rust, reason = False, "make rustavailable failed with the GCC toolchain"
    elif rust and btf and lto != "none":
        rust, reason = False, "Kconfig: RUST depends on !DEBUG_INFO_BTF || (PAHOLE_HAS_LANG_EXCLUDE && !LTO); BTF (sched_ext/BPF) and LTO are both selected"
    fdo, fdo_reason = s["compiler"]["fdo"], ""
    if fdo != "none":
        pdir = Path(s["compiler"]["fdo_profile_dir"]).expanduser() if s["compiler"]["fdo_profile_dir"] else STATE_DIR / "fdo" / p.name
        if toolchain != "llvm":
            fdo, fdo_reason = "none", "AutoFDO requires clang"
        elif not (pdir / "kernel.afdo").is_file():
            fdo, fdo_reason = "none", f"missing {pdir / 'kernel.afdo'} (record one with --fdo-record)"
        elif fdo == "autofdo_propeller" and not ((pdir / "propeller_cc_profile.txt").is_file() and (pdir / "propeller_ld_profile.txt").is_file()):
            fdo, fdo_reason = "autofdo", f"Propeller profiles missing in {pdir}; using AutoFDO only"
    if s["timing"]["preempt"] == "lazy" and not idx.has("PREEMPT_LAZY"):
        raise ProfileError("This tree has no PREEMPT_LAZY -- it is not a Linux >= 7.2 x86-64 tree")
    if s["cache"]["sched_cache"] and not idx.has("SCHED_CACHE"):
        warn("CONFIG_SCHED_CACHE (cache-aware scheduling) is not present in this tree; CAS knobs become no-ops")
    if s["gaming"]["ntsync"] and not idx.has("NTSYNC"):
        warn("CONFIG_NTSYNC is not present in this tree")

    arch = s["cpu"]["arch"]
    custom_march = s["cpu"]["march"].strip()
    if custom_march:
        march = custom_march
        mtune = custom_march
    elif arch == "native":
        march = "native"
        mtune = "native"
    elif arch == "generic":
        march = "x86-64"
        mtune = "generic"
    elif arch.startswith("generic_v"):
        v = arch.split("_v")[1]
        march = f"x86-64-v{v}"
        mtune = "generic"
    elif "x86-64" in arch:
        march = arch.replace("_", "-")
        mtune = "generic"
    else:
        march = arch
        mtune = arch

    return Derived(facts=facts, idx=idx, tree=tree, version=tree_version(tree), sched=sched, toolchain=toolchain, lto=lto, btf=btf,
                   tracing=tracing, rust=rust, rust_reason=reason, fdo=fdo, fdo_reason=fdo_reason, march=march, mtune=mtune)


MANAGED_CMDLINE_KEYS: Final = frozenset({"mitigations", "nosmt", "amd_pstate", "amd_prefcore", "preempt", "cpuidle.governor", "nvme.poll_queues", "zswap.enabled",
                                         "zswap.compressor", "zswap.zpool", "zswap.max_pool_percent", "zswap.shrinker_enabled", "split_lock_detect", "nowatchdog",
                                         "nmi_watchdog", "pcie_aspm.policy", "transparent_hugepage", "rcu_nocbs", "rcutree.enable_rcu_lazy", "pcie_acs_override"})


def flavor_cmdline(p: KernelProfile, facts: HostFacts) -> list[str]:
    """Flavor-specific kernel parameters (baked into CONFIG_CMDLINE and/or the boot entry)."""
    s = p.sections
    out: list[str] = []
    match s["cpu"]["mitigations"]:
        case "off":
            out.append("mitigations=off")
        case "nosmt":
            out.append("mitigations=auto,nosmt")
    if not s["cpu"]["smt"]:
        out.append("nosmt")
    if facts.vendor == "amd" or s["meta"]["portable_package"]:
        mode = s["cpu"]["amd_pstate"]
        if mode != "undefined":
            out.append(f"amd_pstate={mode}")
        if not s["cpu"]["prefcore"]:
            out.append("amd_prefcore=disable")
    if s["timing"]["preempt_dynamic"] and s["timing"]["preempt"] != "rt":
        out.append(f"preempt={s['timing']['preempt']}")
    out.append(f"cpuidle.governor={s['power']['cpu_idle_governor']}")
    if s["storage"]["nvme_poll_queues"]:
        out.append(f"nvme.poll_queues={s['storage']['nvme_poll_queues']}")
    match s["memory"]["swap_backend"]:
        case "zswap":
            out += ["zswap.enabled=1", f"zswap.compressor={s['memory']['zswap_compressor']}", "zswap.zpool=zsmalloc",
                    f"zswap.max_pool_percent={s['memory']['zswap_max_pool_pct']}", "zswap.shrinker_enabled=1"]
        case _:
            out.append("zswap.enabled=0")
    if not s["gaming"]["split_lock_mitigate"]:
        out.append("split_lock_detect=off")
    if s["boot"]["nowatchdog"]:
        out += ["nowatchdog", "nmi_watchdog=0"]
    if s["power"]["pcie_aspm"] != "default":
        out.append(f"pcie_aspm.policy={s['power']['pcie_aspm']}")
    out.append(f"transparent_hugepage={s['memory']['thp']}")
    if s["power"]["rcu_lazy"]:
        out.append("rcutree.enable_rcu_lazy=1")
    if s["boot"].get("acs_override", False):
        out.append("pcie_acs_override=downstream,multifunction")
    out += shlex.split(s["boot"]["cmdline_extra"])
    return out


# ---------------------------------------------------------------------------------------------------
# Kconfig matrix
# ---------------------------------------------------------------------------------------------------
def _ops_uarch(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    arch = p.g("cpu", "arch")
    for sym in GRAYSKY_SYMBOLS:
        mx.n(sym, optional=True)
    mx.n("X86_NATIVE_CPU", optional=True)
    if arch == "native":
        if d.idx.has("X86_NATIVE_CPU"):
            mx.y("X86_NATIVE_CPU", why="-march=native")
        else:
            d.kcflags.append("-march=native")
        mx.y("GENERIC_CPU", optional=True)
        mx.val("X86_64_VERSION", min(d.facts.psabi_level, d.idx.x86_64_version_max), optional=True)
        if d.rust:
            d.krustflags.append("-Ctarget-cpu=native")
        return
    level, gsym = UARCH_INFO[arch]
    if gsym != "GENERIC_CPU" and d.idx.has(gsym):
        mx.y(gsym, why=f"more-uarches patch present: {gsym}")
        return
    mx.y("GENERIC_CPU", optional=True)
    mx.val("X86_64_VERSION", min(level, d.idx.x86_64_version_max), optional=True, why=f"psABI level for {arch}")
    if not arch.startswith("generic"):
        d.kcflags += [f"-march={arch}", f"-mtune={arch}"]
        if d.rust:
            d.krustflags.append(f"-Ctarget-cpu={arch}")


def _ops_core(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s, f = p.sections, d.facts
    lean = p.lean("lean")
    for sym in ("EXPERT", "MULTIUSER", "POSIX_MQUEUE", "SYSVIPC", "NAMESPACES", "USER_NS", "PID_NS", "NET_NS", "UTS_NS", "IPC_NS", "TIME_NS",
                "CGROUPS", "CGROUP_BPF", "CGROUP_SCHED", "FAIR_GROUP_SCHED", "CFS_BANDWIDTH", "CGROUP_FREEZER", "CGROUP_PIDS", "CGROUP_DEVICE",
                "CGROUP_CPUACCT", "CGROUP_PERF", "CGROUP_MISC", "CPUSETS", "SECCOMP", "SECCOMP_FILTER", "SECURITY", "SECURITYFS", "SECURITY_LANDLOCK",
                "SECURITY_YAMA", "SECURITY_LOCKDOWN_LSM", "LOCK_DOWN_KERNEL_FORCE_NONE", "EPOLL", "SIGNALFD", "TIMERFD", "EVENTFD", "FHANDLE",
                "INOTIFY_USER", "FANOTIFY", "FANOTIFY_ACCESS_PERMISSIONS", "IO_URING", "ADVISE_SYSCALLS", "MEMBARRIER", "RSEQ", "KCMP", "FUTEX", "FUTEX_PI",
                "DEVTMPFS", "DEVTMPFS_MOUNT", "TMPFS", "TMPFS_POSIX_ACL", "TMPFS_XATTR", "TMPFS_INODE64", "PROC_FS", "PROC_SYSCTL", "PROC_PAGE_MONITOR",
                "SYSFS", "CONFIGFS_FS", "EFIVAR_FS", "EFI", "EFI_STUB", "EFI_HANDOVER_PROTOCOL", "BLK_DEV_INITRD", "RD_ZSTD", "RD_GZIP", "MODULES",
                "MODULE_UNLOAD", "KERNEL_ZSTD", "RELOCATABLE", "RANDOMIZE_BASE", "RANDOMIZE_MEMORY", "MICROCODE", "DMI", "DMIID", "ACPI",
                "PCI", "PCI_MSI", "PCIEPORTBUS", "PCIEAER", "PCIEASPM", "HOTPLUG_PCI", "HOTPLUG_PCI_PCIE", "VT", "VT_CONSOLE", "UNIX98_PTYS", "FW_LOADER",
                "FW_LOADER_COMPRESS", "FW_LOADER_COMPRESS_ZSTD", "FW_LOADER_COMPRESS_XZ", "SYSFB_SIMPLEFB", "DRM_FBDEV_EMULATION",
                "FRAMEBUFFER_CONSOLE", "FRAMEBUFFER_CONSOLE_DETECT_PRIMARY", "KALLSYMS", "BINFMT_ELF", "BINFMT_SCRIPT", "COREDUMP", "PERF_EVENTS", "HWMON",
                "THERMAL", "NLS", "NLS_UTF8", "UNICODE", "AUTOFS_FS", "KEYS", "BLK_DEV_DM", "EFI_PARTITION", "MSDOS_PARTITION", "SWAP", "SHMEM", "AIO",
                "UNIX", "INET", "IPV6", "NETFILTER", "PACKET", "CRYPTO_USER_API_HASH", "CRYPTO_USER_API_SKCIPHER", "CRYPTO_USER_API_RNG", "CRYPTO_USER_API_AEAD",
                "INTEGRITY"):
        mx.y(sym)
    for sym in ("BINFMT_MISC", "BLK_DEV_LOOP", "FUSE_FS", "OVERLAY_FS", "DM_CRYPT", "DM_INTEGRITY", "X86_MSR", "X86_CPUID"):
        mx.m(sym)
    comp = {"zstd": "RD_ZSTD", "xz": "RD_XZ", "lz4": "RD_LZ4", "gzip": "RD_GZIP", "lzma": "RD_LZMA", "bzip2": "RD_BZIP2", "lzo": "RD_LZO"}
    mx.y(comp.get(f.initrd_compression, "RD_ZSTD"), why=f"mkinitcpio COMPRESSION={f.initrd_compression}")
    mx.y("X86_X2APIC", optional=True, why="needs IRQ_REMAP or HYPERVISOR_GUEST")
    mx.n("MICROCODE_LATE_LOADING")
    mx.n("X86_EXTENDED_PLATFORM")
    mx.flag("EFI_MIXED", not lean)
    mx.flag("IKHEADERS", False)
    mx.flag("IMA", False)
    mx.flag("EVM", False)
    mx.n("WERROR")
    for werr in ("DRM_WERROR", "DRM_AMDGPU_WERROR", "DRM_XE_WERROR", "DRM_I915_WERROR", "KVM_WERROR"):
        mx.n(werr, optional=True)
    mx.s("SYSTEM_TRUSTED_KEYS", "")
    mx.s("SYSTEM_REVOCATION_KEYS", "")
    mx.flag("FRAMEBUFFER_CONSOLE_DEFERRED_TAKEOVER", not s["dusky"]["enhanced"])
    mx.flag("DYNAMIC_DEBUG", not lean)
    mx.flag("PROFILING", not lean)
    mx.flag("BSD_PROCESS_ACCT", not lean)
    mx.flag("SYSFS_SYSCALL", False)
    mx.flag("PCSPKR_PLATFORM", not lean)
    mx.flag("X86_16BIT", not lean)


def _ops_sched(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s = p.sections
    for sym in ("SCHED_BORE", "SCHED_ALT", "SCHED_BMQ", "SCHED_PDS"):
        mx.n(sym, optional=True)
    match d.sched:
        case "bore":
            mx.y("SCHED_BORE", why="BORE patch")
            mx.val("MIN_BASE_SLICE_NS", 1000000, optional=True)
        case "bmq":
            mx.y("SCHED_ALT", why="Project C")
            mx.y("SCHED_BMQ")
    mx.flag("SCHED_AUTOGROUP", s["scheduler"]["autogroup"])
    mx.flag("RT_GROUP_SCHED", s["scheduler"]["rt_group"])
    mx.flag("SCHED_CORE", s["scheduler"]["sched_core"])
    mx.y("SCHED_MC")
    mx.flag("SCHED_MC_PRIO", s["cpu"]["prefcore"])
    mx.flag("SCHED_SMT", s["cpu"]["smt"])
    mx.y("SCHED_CLUSTER")
    mx.flag("SCHED_CACHE", s["cache"]["sched_cache"], why="Cache-aware scheduling")
    mx.flag("UCLAMP_TASK", s["gaming"]["uclamp"])
    mx.flag("UCLAMP_TASK_GROUP", s["gaming"]["uclamp"])
    mx.flag("RSEQ_SLICE_EXTENSION", s["rseq"]["slice_extension"], optional=True)
    mx.y("PSI")
    mx.n("PSI_DEFAULT_DISABLED")
    mx.flag("SCHEDSTATS", d.tracing == "full" and not p.lean("lean"))
    mx.y("CPU_FREQ_GOV_SCHEDUTIL")
    if d.scx_class and s["scheduler"]["scx_enable_class"]:
        for sym in ("BPF", "BPF_SYSCALL", "BPF_JIT", "BPF_JIT_ALWAYS_ON", "BPF_JIT_DEFAULT_ON", "SCHED_CLASS_EXT", "DEBUG_INFO_BTF", "DEBUG_INFO_BTF_MODULES",
                    "BPF_UNPRIV_DEFAULT_OFF", "CGROUP_BPF"):
            mx.y(sym, why="sched_ext")
        mx.y("PAHOLE_HAS_SPLIT_BTF", optional=True)
        mx.n("MODULE_ALLOW_BTF_MISMATCH")
    else:
        mx.n("SCHED_CLASS_EXT")
    if d.tracing == "full":
        for sym in ("FTRACE", "TRACEPOINTS", "STACKTRACE", "FUNCTION_TRACER", "DYNAMIC_FTRACE", "FUNCTION_GRAPH_TRACER", "FPROBE", "KPROBES", "KPROBE_EVENTS",
                    "UPROBES", "UPROBE_EVENTS", "BPF_EVENTS", "BPF_SYSCALL", "BPF_JIT", "PERF_EVENTS", "BPF_LSM"):
            mx.y(sym, why="tracing=full")
        for sym in ("FTRACE_SYSCALLS", "STACK_TRACER", "BPF_KPROBE_OVERRIDE", "MMIOTRACE", "FUNCTION_PROFILER", "HWLAT_TRACER", "OSNOISE_TRACER", "TIMERLAT_TRACER"):
            mx.n(sym)
    else:
        for sym in ("FUNCTION_TRACER", "DYNAMIC_FTRACE", "FUNCTION_GRAPH_TRACER", "FPROBE", "KPROBES", "KPROBE_EVENTS", "UPROBES", "UPROBE_EVENTS", "BPF_EVENTS",
                    "STACK_TRACER", "BLK_DEV_IO_TRACE", "FTRACE_SYSCALLS", "SCHED_TRACER", "IRQSOFF_TRACER", "PREEMPT_TRACER", "HWLAT_TRACER", "OSNOISE_TRACER",
                    "TIMERLAT_TRACER", "MMIOTRACE", "SYNTH_EVENTS", "HIST_TRIGGERS", "BOOTTIME_TRACING", "FUNCTION_PROFILER", "KPROBE_EVENTS_ON_NOTRACE", "FTRACE"):
            mx.n(sym, why="tracing=minimal")
        mx.n("BPF_LSM", optional=True, why="needs BPF_EVENTS")
        mx.y("BPF_SYSCALL")
        mx.y("BPF_JIT")


def _ops_cpu(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s, f = p.sections, d.facts
    c = s["cpu"]
    lean = p.lean("lean")
    _ops_uarch(mx, p, d)
    if c["nr_cpus"]:
        nr = c["nr_cpus"]
    elif s["meta"]["portable_package"]:
        nr = 512
    else:
        nr = max(8, ((f.threads + 7) // 8) * 8)
    mx.val("NR_CPUS", nr)
    mx.n("MAXSMP")
    mx.flag("CPUMASK_OFFSTACK", nr > 512)
    mx.flag("X86_MCE", c["mce"])
    mx.flag("X86_MCE_AMD", c["mce"] and f.vendor != "intel")
    mx.flag("X86_MCE_INTEL", c["mce"] and f.vendor != "amd")
    mx.n("X86_MCELOG_LEGACY")
    mx.flag("CPU_MITIGATIONS", c["mitigations"] != "off", why=f"cpu.mitigations={c['mitigations']}")
    mx.flag("IA32_EMULATION", c["compat32"])
    mx.n("X86_X32_ABI")
    mx.y("CPU_FREQ")
    mx.y("CPU_FREQ_STAT")
    govs = ("PERFORMANCE", "POWERSAVE", "USERSPACE", "ONDEMAND", "CONSERVATIVE", "SCHEDUTIL")
    for g in govs:
        keep = not lean or g in ("PERFORMANCE", "POWERSAVE", "SCHEDUTIL") or g == c["governor"].upper()
        mx.flag("CPU_FREQ_GOV_" + g, keep)
    mx.choice([f"CPU_FREQ_DEFAULT_GOV_{g}" for g in govs], f"CPU_FREQ_DEFAULT_GOV_{c['governor'].upper()}")
    mx.y("X86_INTEL_PSTATE")
    mx.y("X86_AMD_PSTATE")
    mx.n("X86_AMD_PSTATE_UT")
    mx.m("X86_ACPI_CPUFREQ")
    mx.y("X86_ACPI_CPUFREQ_CPB")
    if c["amd_pstate"] != "undefined":
        mx.val("X86_AMD_PSTATE_DEFAULT_MODE", {"disable": 1, "passive": 2, "active": 3, "guided": 4}[c["amd_pstate"]], why=f"amd_pstate={c['amd_pstate']}")
    gov = s["power"]["cpu_idle_governor"]
    mx.y("CPU_IDLE")
    mx.flag("CPU_IDLE_GOV_TEO", gov == "teo")
    mx.flag("CPU_IDLE_GOV_MENU", gov == "menu")
    mx.n("CPU_IDLE_GOV_LADDER")
    mx.flag("CPU_IDLE_GOV_HALTPOLL", gov == "haltpoll" or f.virt != "none", optional=True, why="KVM guests only")
    mx.flag("HALTPOLL_CPUIDLE", f.virt != "none", optional=True)
    mx.flag("INTEL_IDLE", f.vendor != "amd" or s["meta"]["portable_package"])
    portable = s["meta"]["portable_package"]
    if f.vendor == "intel" or portable:
        mx.y("INTEL_HFI_THERMAL", optional=True, why="Intel Thread Director feedback")
        mx.m("INTEL_TCC_COOLING", optional=True)
        mx.m("INTEL_RAPL", optional=True)
        mx.y("X86_INTEL_LPSS")
        mx.m("INTEL_PMC_CORE", optional=True)
        mx.m("INTEL_UNCORE_FREQ_CONTROL", optional=True)
    if f.vendor == "amd" or portable:
        mx.m("AMD_PMC", optional=True)
        mx.m("AMD_PMF", optional=True)
        mx.y("X86_AMD_PLATFORM_DEVICE")
        mx.m("SENSORS_K10TEMP")
        mx.m("AMD_3D_VCACHE", optional=True)
        mx.n("AMD_HSMP")
        mx.flag("AMD_NUMA", s["memory"]["numa"])
    mx.flag("X86_CPU_RESCTRL", not lean, optional=True)
    vsys = "LEGACY_VSYSCALL_NONE" if (s["security"]["profile"] == "hardened" or lean) else "LEGACY_VSYSCALL_XONLY"
    mx.choice(("LEGACY_VSYSCALL_XONLY", "LEGACY_VSYSCALL_NONE"), vsys)


def _ops_timing(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s = p.sections
    t = s["timing"]
    hz = t["hz"]
    mx.choice([f"HZ_{h}" for h in HZ_CHOICES], f"HZ_{hz}", why=f"{hz} Hz")
    mx.val("HZ", hz)
    match t["tickless"]:
        case "periodic":
            mx.y("HZ_PERIODIC")
            for sym in ("NO_HZ_IDLE", "NO_HZ_FULL", "NO_HZ"):
                mx.n(sym)
        case "idle":
            mx.n("HZ_PERIODIC")
            mx.y("NO_HZ_IDLE")
            mx.n("NO_HZ_FULL")
            mx.y("NO_HZ")
            mx.y("NO_HZ_COMMON")
        case "full":
            mx.n("HZ_PERIODIC")
            mx.n("NO_HZ_IDLE")
            mx.y("NO_HZ_FULL")
            mx.y("NO_HZ")
            mx.y("NO_HZ_COMMON")
            mx.y("CONTEXT_TRACKING_USER")
            mx.n("CONTEXT_TRACKING_USER_FORCE")
            mx.y("VIRT_CPU_ACCOUNTING_GEN")
            mx.y("CPU_ISOLATION")
    sel = {"lazy": "PREEMPT_LAZY", "full": "PREEMPT", "rt": "PREEMPT_RT"}[t["preempt"]]
    mx.choice(("PREEMPT_NONE", "PREEMPT_VOLUNTARY", "PREEMPT", "PREEMPT_LAZY", "PREEMPT_RT"), sel, why=f"preempt={t['preempt']}")
    mx.flag("PREEMPT_DYNAMIC", t["preempt_dynamic"] and t["preempt"] != "rt")
    mx.y("HIGH_RES_TIMERS")
    mx.y("POSIX_TIMERS")
    mx.y("IRQ_TIME_ACCOUNTING")
    if s["power"]["rcu_lazy"]:
        mx.y("RCU_EXPERT")
        mx.y("RCU_NOCB_CPU")
        mx.y("RCU_NOCB_CPU_DEFAULT_ALL")
        mx.y("RCU_LAZY", why="battery: lazy RCU callbacks")
        mx.n("RCU_LAZY_DEFAULT_OFF")
    else:
        mx.n("RCU_LAZY", optional=True)


def _ops_memory(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s, f = p.sections, d.facts
    m, sec = s["memory"], s["security"]
    lean, minimal, embedded = p.lean("lean"), p.lean("minimal"), p.lean("embedded")
    hardened, extreme = sec["profile"] == "hardened", sec["profile"] == "extreme"
    vm = f.virt != "none"
    thp = m["thp"]
    mx.y("TRANSPARENT_HUGEPAGE")
    mx.choice(("TRANSPARENT_HUGEPAGE_ALWAYS", "TRANSPARENT_HUGEPAGE_MADVISE", "TRANSPARENT_HUGEPAGE_NEVER"), f"TRANSPARENT_HUGEPAGE_{thp.upper()}", why=f"thp={thp}")
    mx.flag("THP_SWAP", thp != "never")
    mx.flag("READ_ONLY_THP_FOR_FS", thp != "never" and not lean)
    mx.flag("HUGETLBFS", m["hugetlbfs"])
    mx.flag("HUGETLB_PAGE", m["hugetlbfs"])
    mx.flag("LRU_GEN", m["mglru"])
    mx.flag("LRU_GEN_ENABLED", m["mglru"])
    mx.n("LRU_GEN_STATS")
    mx.y("SWAP")
    mx.y("ZSMALLOC")
    mx.n("ZSMALLOC_STAT")
    zdef = {"zstd": "ZRAM_DEF_COMP_ZSTD", "lz4": "ZRAM_DEF_COMP_LZ4", "lz4hc": "ZRAM_DEF_COMP_LZ4HC", "lzo-rle": "ZRAM_DEF_COMP_LZORLE"}
    match m["swap_backend"]:
        case "zram":
            algos = {m["zram_algo"], m["zram_recomp_algo"] if m["zram_multi_comp"] else m["zram_algo"]}
            mx.y("ZRAM", why="swap_backend=zram")
            mx.y("ZRAM_BACKEND_ZSTD")
            mx.y("ZRAM_BACKEND_LZ4")
            mx.flag("ZRAM_BACKEND_LZ4HC", "lz4hc" in algos)
            mx.flag("ZRAM_BACKEND_LZO", "lzo-rle" in algos)
            mx.n("ZRAM_BACKEND_DEFLATE")
            mx.n("ZRAM_BACKEND_842")
            mx.choice(zdef.values(), zdef[m["zram_algo"]])
            mx.flag("ZRAM_MULTI_COMP", m["zram_multi_comp"], why="zram recompression")
            mx.flag("ZRAM_TRACK_ENTRY_ACTIME", m["zram_multi_comp"])
            mx.y("ZRAM_WRITEBACK")
            mx.n("ZRAM_MEMORY_TRACKING")
            mx.flag("ZSWAP", not lean)
            mx.n("ZSWAP_DEFAULT_ON", optional=True)
        case "zswap":
            comp = m["zswap_compressor"]
            mx.y("ZSWAP", why="swap_backend=zswap")
            mx.y("ZSWAP_DEFAULT_ON")
            mx.y("ZSWAP_SHRINKER_DEFAULT_ON")
            zc = {"zstd": "ZSWAP_COMPRESSOR_DEFAULT_ZSTD", "lz4": "ZSWAP_COMPRESSOR_DEFAULT_LZ4", "lz4hc": "ZSWAP_COMPRESSOR_DEFAULT_LZ4HC", "lzo": "ZSWAP_COMPRESSOR_DEFAULT_LZO"}
            mx.choice(zc.values(), zc[comp])
            mx.y(f"CRYPTO_{comp.upper()}")
            mx.y("ZSWAP_ZPOOL_DEFAULT_ZSMALLOC", optional=True)
            mx.m("ZRAM")
        case _:
            mx.n("ZSWAP_DEFAULT_ON", optional=True)
            mx.flag("ZSWAP", not lean)
            if lean:
                mx.n("ZRAM")
            else:
                mx.m("ZRAM")
    tiny = m["slub_tiny"]
    mx.y("SLUB")
    mx.flag("SLUB_TINY", tiny, why="minimal allocator footprint")
    mx.flag("SLUB_CPU_PARTIAL", not tiny and not lean and s["timing"]["preempt"] != "rt", optional=tiny)
    mx.flag("SLUB_DEBUG", not lean and not tiny)
    mx.n("SLUB_DEBUG_ON")
    mx.n("SLUB_STATS")
    mx.flag("SLAB_MERGE_DEFAULT", not hardened)
    mx.flag("SLAB_FREELIST_HARDENED", sec["slab_freelist_hardened"] and not tiny, why="depends on !SLUB_TINY")
    mx.flag("SLAB_FREELIST_RANDOM", sec["slab_freelist_random"] and not tiny, why="depends on !SLUB_TINY")
    mx.flag("SLAB_BUCKETS", m["slab_buckets"] and not tiny)
    mx.flag("RANDOM_KMALLOC_CACHES", hardened and not lean and not tiny)
    mx.flag("SHUFFLE_PAGE_ALLOCATOR", not extreme and not lean)
    mx.flag("PER_VMA_LOCK", m["per_vma_lock"], optional=True)
    mx.y("COMPACTION")
    mx.y("MIGRATION")
    mx.flag("KSM", m["ksm"])
    for sym in ("DAMON", "DAMON_VADDR", "DAMON_PADDR", "DAMON_SYSFS", "DAMON_RECLAIM", "DAMON_LRU_SORT"):
        mx.flag(sym, m["damon"])
    mx.flag("PAGE_REPORTING", m["page_reporting"])
    if m["numa"]:
        mx.y("NUMA")
        mx.y("X86_64_ACPI_NUMA")
        mx.val("NODES_SHIFT", m["nodes_shift"])
        mx.flag("NUMA_BALANCING", m["numa_balancing"])
        mx.flag("NUMA_BALANCING_DEFAULT_ENABLED", m["numa_balancing"])
        mx.n("NUMA_EMU")
    else:
        mx.n("NUMA", why="single-node host")
    mx.flag("MEMCG", m["memcg"])
    mx.flag("MEMCG_V1", m["memcg"] and not lean)
    mx.flag("CPUSETS_V1", not lean)
    hotplug = vm or not lean or bool(set(f.gpus) & {"amd", "nvidia"})
    mx.flag("MEMORY_HOTPLUG", hotplug, why="ZONE_DEVICE/DEVICE_PRIVATE (GPU SVM) and VM balloons need it")
    mx.flag("MEMORY_HOTREMOVE", hotplug)
    mx.flag("ZONE_DEVICE", hotplug and not embedded, optional=True)
    log_shift = m["log_buf_shift"] or (15 if minimal else 16 if lean else 17)
    mx.val("LOG_BUF_SHIFT", log_shift)
    mx.val("LOG_CPU_MAX_BUF_SHIFT", 12)
    mx.val("PRINTK_SAFE_LOG_BUF_SHIFT", 13)
    mx.flag("KALLSYMS_ALL", m["kallsyms_all"])
    mx.flag("IKCONFIG", m["ikconfig"])
    mx.flag("IKCONFIG_PROC", m["ikconfig"])
    mx.flag("BASE_SMALL", m["base_small"])
    mx.flag("BASE_FULL", not m["base_small"])
    mx.flag("KEXEC", m["kexec"])
    mx.flag("KEXEC_FILE", m["kexec"])
    mx.flag("CRASH_DUMP", m["kexec"] and not lean)
    mx.flag("PROC_VMCORE", m["kexec"] and not lean)
    mx.flag("PROC_KCORE", not lean)
    mx.flag("VM_EVENT_COUNTERS", not embedded)
    mx.n("PERCPU_STATS")
    mx.flag("TRIM_UNUSED_KSYMS", m["trim_unused_ksyms"], why="dead export elimination")
    mx.flag("LD_DEAD_CODE_DATA_ELIMINATION", m["dead_code_elimination"], optional=True, why="requires HAS_LD_DEAD_CODE_DATA_ELIMINATION (not selected by x86)")
    for sym in ("PAGE_POISONING", "DEBUG_PAGEALLOC", "PAGE_OWNER", "PAGE_TABLE_CHECK", "DEBUG_VM", "MEMTEST", "KASAN", "KMSAN", "KCSAN", "LOCKDEP", "PROVE_LOCKING",
                "DEBUG_ATOMIC_SLEEP", "DEBUG_PREEMPT", "DEBUG_KMEMLEAK", "DEBUG_OBJECTS", "DEBUG_STACK_USAGE", "LATENCYTOP", "DEBUG_MISC", "PRINTK_INDEX",
                "SLUB_DEBUG_ON", "HWPOISON_INJECT", "FAULT_INJECTION", "DEBUG_PER_CPU_MAPS", "DEBUG_TIMEKEEPING", "DEBUG_SG", "DEBUG_PLIST"):
        mx.n(sym)
    kfence = not extreme and not lean and not tiny
    mx.flag("KFENCE", kfence, why="pool is only reserved when kfence.sample_interval > 0")
    if kfence:
        mx.val("KFENCE_SAMPLE_INTERVAL", 0)
    mx.flag("UBSAN", sec["ubsan_bounds"])
    mx.flag("UBSAN_BOUNDS", sec["ubsan_bounds"])
    mx.n("UBSAN_TRAP")
    mx.n("UBSAN_SHIFT")
    mx.n("UBSAN_DIV_ZERO")
    mx.n("UBSAN_BOOL")
    mx.n("UBSAN_ENUM")
    mx.n("UBSAN_ALIGNMENT")
    mx.flag("UBSAN_SANITIZE_ALL", sec["ubsan_bounds"], optional=True)


def _ops_compiler(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s = p.sections
    c, sec = s["compiler"], s["security"]
    extreme, hardened = sec["profile"] == "extreme", sec["profile"] == "hardened"
    if c["optimize"] == "o3" and d.idx.has("CC_OPTIMIZE_FOR_PERFORMANCE_O3"):
        mx.choice(("CC_OPTIMIZE_FOR_PERFORMANCE", "CC_OPTIMIZE_FOR_PERFORMANCE_O3", "CC_OPTIMIZE_FOR_SIZE"), "CC_OPTIMIZE_FOR_PERFORMANCE_O3", why="optimize=o3")
    elif c["optimize"] == "size":
        mx.choice(("CC_OPTIMIZE_FOR_PERFORMANCE", "CC_OPTIMIZE_FOR_SIZE"), "CC_OPTIMIZE_FOR_SIZE", why="optimize=size")
    else:
        mx.choice(("CC_OPTIMIZE_FOR_PERFORMANCE", "CC_OPTIMIZE_FOR_SIZE"), "CC_OPTIMIZE_FOR_PERFORMANCE", why="optimize=o2")
    if bool(c.get("polly", False)) and d.toolchain == "llvm":
        mx.y("POLLY_CLANG", optional=True, why="Clang Polly loop optimizer")
    if d.toolchain == "llvm":
        mx.choice(("LTO_NONE", "LTO_CLANG_THIN", "LTO_CLANG_FULL"), {"none": "LTO_NONE", "thin": "LTO_CLANG_THIN", "full": "LTO_CLANG_FULL"}[d.lto], why=f"lto={d.lto}")
    cfi_sym = "CFI" if d.idx.has("CFI") else "CFI_CLANG"
    cfi = bool(c["kcfi"]) and d.toolchain == "llvm"
    mx.flag(cfi_sym, cfi, why="kCFI")
    if cfi:
        mx.n("CFI_PERMISSIVE")
        mx.y("CFI_AUTO_DEFAULT", optional=True)
        mx.y("X86_KERNEL_IBT")
        mx.flag("CFI_ICALL_NORMALIZE_INTEGERS", d.rust, optional=True)
    else:
        mx.flag("X86_KERNEL_IBT", not extreme)
    mx.flag("AUTOFDO_CLANG", d.fdo in ("autofdo", "autofdo_propeller"), why="AutoFDO")
    mx.flag("PROPELLER_CLANG", d.fdo == "autofdo_propeller", why="Propeller")
    dwarf = ("DEBUG_INFO_NONE", "DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT", "DEBUG_INFO_DWARF4", "DEBUG_INFO_DWARF5")
    if d.btf:
        mx.choice(dwarf, "DEBUG_INFO_DWARF5", why="BTF needs DWARF")
        mx.n("DEBUG_INFO_REDUCED")
        mx.n("DEBUG_INFO_SPLIT")
        mx.y("DEBUG_INFO_BTF")
        mx.y("DEBUG_INFO_BTF_MODULES")
        mx.choice(("DEBUG_INFO_COMPRESSED_NONE", "DEBUG_INFO_COMPRESSED_ZLIB", "DEBUG_INFO_COMPRESSED_ZSTD"), "DEBUG_INFO_COMPRESSED_ZSTD", optional=True)
    else:
        match c["debug_info"]:
            case "none":
                mx.choice(dwarf, "DEBUG_INFO_NONE", why="debug_info=none")
                mx.n("DEBUG_INFO_BTF", optional=True)
            case "reduced":
                mx.choice(dwarf, "DEBUG_INFO_DWARF5")
                mx.y("DEBUG_INFO_REDUCED")
                mx.n("DEBUG_INFO_BTF", optional=True)
            case _:
                mx.choice(dwarf, "DEBUG_INFO_DWARF5")
                mx.n("DEBUG_INFO_REDUCED")
                mx.n("DEBUG_INFO_BTF", optional=True)
    if c["module_compress"] == "none":
        mx.n("MODULE_COMPRESS")
    else:
        mx.y("MODULE_COMPRESS")
        mx.choice(("MODULE_COMPRESS_GZIP", "MODULE_COMPRESS_XZ", "MODULE_COMPRESS_ZSTD"), f"MODULE_COMPRESS_{c['module_compress'].upper()}")
        mx.y("MODULE_COMPRESS_ALL")
        mx.y("MODULE_DECOMPRESS")
    mx.flag("RUST", d.rust, why=d.rust_reason or "Rust for Linux")
    if d.rust:
        mx.n("RUST_DEBUG_ASSERTIONS")
        mx.y("RUST_OVERFLOW_CHECKS")
        mx.n("SAMPLES_RUST")
    mx.flag("MODVERSIONS", c["modversions"])
    mx.n("MODULE_SRCVERSION_ALL")
    mx.n("MODULE_FORCE_LOAD")
    mx.n("MODULE_DEBUG")
    mx.n("MODULE_STATS")
    if d.toolchain == "llvm":
        mx.n("GCC_PLUGINS")
    mx.choice(("RANDSTRUCT_NONE", "RANDSTRUCT_FULL", "RANDSTRUCT_PERFORMANCE"), "RANDSTRUCT_NONE")
    mx.choice(("UNWINDER_ORC", "UNWINDER_FRAME_POINTER"), "UNWINDER_ORC")
    mx.choice(("INIT_STACK_NONE", "INIT_STACK_ALL_PATTERN", "INIT_STACK_ALL_ZERO"), "INIT_STACK_NONE" if extreme else "INIT_STACK_ALL_ZERO")
    mx.flag("ZERO_CALL_USED_REGS", hardened)
    mx.flag("FORTIFY_SOURCE", not extreme)
    mx.flag("BUG_ON_DATA_CORRUPTION", not extreme)
    mx.flag("LIST_HARDENED", not extreme)
    if s["boot"]["cmdline"] == "bake":
        params = flavor_cmdline(p, d.facts)
        if params:
            mx.y("CMDLINE_BOOL", why="baked flavor command line")
            mx.s("CMDLINE", " ".join(params))
            mx.n("CMDLINE_OVERRIDE")
    else:
        mx.n("CMDLINE_BOOL", optional=True)


def _ops_security(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s = p.sections
    sec = s["security"]
    hardened = sec["profile"] == "hardened"
    mx.flag("HARDENED_USERCOPY", sec["hardened_usercopy"])
    mx.flag("INIT_ON_ALLOC_DEFAULT_ON", sec["init_on_alloc"])
    mx.flag("INIT_ON_FREE_DEFAULT_ON", sec["init_on_free"])
    match sec["stackprotector"]:
        case "strong":
            mx.y("STACKPROTECTOR")
            mx.y("STACKPROTECTOR_STRONG")
        case "regular":
            mx.y("STACKPROTECTOR")
            mx.n("STACKPROTECTOR_STRONG")
        case _:
            mx.n("STACKPROTECTOR")
            mx.n("STACKPROTECTOR_STRONG")
    mx.y("RANDOMIZE_KSTACK_OFFSET")
    mx.flag("RANDOMIZE_KSTACK_OFFSET_DEFAULT", sec["randomize_kstack"])
    mx.y("STRICT_KERNEL_RWX")
    mx.y("STRICT_MODULE_RWX")
    mx.y("STRICT_DEVMEM")
    mx.flag("IO_STRICT_DEVMEM", hardened)
    mx.flag("SECURITY_APPARMOR", sec["apparmor"])
    mx.flag("SECURITY_SELINUX", sec["selinux"])
    mx.n("SECURITY_SMACK")
    mx.n("SECURITY_TOMOYO")
    mx.n("SECURITY_APPARMOR_DEBUG")
    mx.s("LSM", "landlock,lockdown,yama,integrity" + (",apparmor" if sec["apparmor"] else "") + ",bpf")
    mx.flag("SECURITY_LOCKDOWN_LSM_EARLY", sec["lockdown_early"])
    mx.flag("SECURITY_DMESG_RESTRICT", hardened)
    mx.y("X86_USER_SHADOW_STACK")
    mx.choice(("IOMMU_DEFAULT_DMA_STRICT", "IOMMU_DEFAULT_DMA_LAZY", "IOMMU_DEFAULT_PASSTHROUGH"), "IOMMU_DEFAULT_DMA_STRICT" if hardened else "IOMMU_DEFAULT_DMA_LAZY")
    mx.choice(("X86_INTEL_TSX_MODE_OFF", "X86_INTEL_TSX_MODE_ON", "X86_INTEL_TSX_MODE_AUTO"), "X86_INTEL_TSX_MODE_OFF" if hardened else "X86_INTEL_TSX_MODE_AUTO")
    mx.flag("SCHED_STACK_END_CHECK", sec["profile"] != "extreme")
    mx.flag("DEBUG_LIST", hardened)
    mx.flag("DEBUG_NOTIFIERS", hardened)
    mx.n("STATIC_USERMODEHELPER")
    mx.y("BPF_UNPRIV_DEFAULT_OFF")
    if s["modules"]["sig_force"]:
        for sym in ("MODULE_SIG", "MODULE_SIG_FORCE", "MODULE_SIG_ALL", "MODULE_SIG_SHA512"):
            mx.y(sym, why="module signature enforcement")
        mx.s("MODULE_SIG_KEY", "certs/signing_key.pem")
        mx.s("MODULE_SIG_HASH", "sha512", optional=True)
    else:
        mx.n("MODULE_SIG_FORCE", optional=True)
        mx.flag("MODULE_SIG", hardened, optional=True)


def _ops_gaming(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    g = p.sections["gaming"]
    if g["ntsync"]:
        mx.m("NTSYNC", why="in-tree NT synchronization primitives")
    else:
        mx.n("NTSYNC")
    mx.y("INPUT_EVDEV")
    if g["controllers"]:
        mx.y("INPUT_JOYSTICK")
        mx.y("HIDRAW")
        mx.y("HID_GENERIC")
        mx.y("USB_HID")
        for sym in ("INPUT_UINPUT", "INPUT_JOYDEV", "JOYSTICK_XPAD", "HID_PLAYSTATION", "HID_SONY", "HID_NINTENDO", "HID_STEAM", "HID_MICROSOFT",
                    "HID_LOGITECH", "HID_LOGITECH_DJ", "HID_LOGITECH_HIDPP", "HID_MULTITOUCH", "INPUT_FF_MEMLESS", "HID_APPLE", "HID_WACOM"):
            mx.m(sym, why="controllers")
        for sym in ("JOYSTICK_XPAD_FF", "JOYSTICK_XPAD_LEDS", "PLAYSTATION_FF", "SONY_FF", "NINTENDO_FF", "STEAM_FF", "LOGITECH_FF", "LOGIWHEELS_FF", "LOGIG940_FF", "LOGIRUMBLEPAD2_FF"):
            mx.y(sym, optional=True)
    mx.flag("USER_EVENTS", d.tracing == "full", optional=True)


FS_SYMBOLS: Final[dict[str, tuple[str, ...]]] = {
    "ext4": ("EXT4_FS", "EXT4_FS_POSIX_ACL", "EXT4_FS_SECURITY"), "btrfs": ("BTRFS_FS", "BTRFS_FS_POSIX_ACL"), "xfs": ("XFS_FS", "XFS_POSIX_ACL"),
    "f2fs": ("F2FS_FS", "F2FS_FS_POSIX_ACL", "F2FS_FS_SECURITY"), "vfat": ("VFAT_FS", "FAT_FS", "NLS_CODEPAGE_437", "NLS_ISO8859_1", "FAT_DEFAULT_UTF8"),
    "exfat": ("EXFAT_FS",), "ntfs3": ("NTFS3_FS", "NTFS3_LZX_XPRESS", "NTFS3_FS_POSIX_ACL"), "fuse": ("FUSE_FS",), "fuseblk": ("FUSE_FS",), "overlay": ("OVERLAY_FS",),
    "nfs": ("NFS_FS", "NFS_V4"), "nfs4": ("NFS_FS", "NFS_V4"), "cifs": ("CIFS",), "smb3": ("CIFS",), "erofs": ("EROFS_FS",), "bcachefs": ("BCACHEFS_FS",),
}


def _ops_storage(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s, f = p.sections, d.facts
    st = s["storage"]
    lean = p.lean("lean")
    if f.has_nvme or st["nvme_poll_queues"]:
        mx.y("BLK_DEV_NVME")
        mx.y("NVME_HWMON")
        mx.n("NVME_MULTIPATH")
    mx.flag("BLK_WBT", st["blk_wbt"])
    mx.flag("BLK_WBT_MQ", st["blk_wbt"])
    mx.y("MQ_IOSCHED_DEADLINE")
    mx.flag("MQ_IOSCHED_KYBER", st["io_scheduler"] == "kyber" or not lean)
    if st["io_scheduler"] == "bfq" or f.rotational:
        mx.y("IOSCHED_BFQ")
        mx.y("BFQ_GROUP_IOSCHED")
    elif lean:
        mx.n("IOSCHED_BFQ")
    else:
        mx.m("IOSCHED_BFQ")
    mx.flag("BLK_CGROUP_IOCOST", st["iocost"])
    mx.n("BLK_CGROUP_IOLATENCY")
    mx.flag("BLK_DEBUG_FS", not lean)
    mx.flag("BLK_SED_OPAL", not lean)
    mx.y("VFAT_FS")
    mx.y("FAT_FS")
    mx.y("NLS_CODEPAGE_437")
    mx.y("NLS_ISO8859_1")
    wanted = set(f.filesystems) | set(st["extra_filesystems"])
    for fstype in sorted(wanted):
        for sym in FS_SYMBOLS.get(fstype, ()):
            if fstype == f.root_fs:
                mx.y(sym, why=f"root filesystem {fstype}")
            elif sym.endswith(("_POSIX_ACL", "_SECURITY", "_UTF8", "NLS_CODEPAGE_437", "NLS_ISO8859_1", "LZX_XPRESS", "NFS_V4")):
                mx.y(sym)
            else:
                mx.m(sym, why=f"filesystem {fstype} in use")
    if f.root_luks:
        mx.y("DM_CRYPT", why="root on dm-crypt")
        for sym in ("CRYPTO_AES", "CRYPTO_XTS", "CRYPTO_SHA256", "CRYPTO_SHA512", "CRYPTO_AES_NI_INTEL"):
            mx.y(sym)
    mx.y("FS_ENCRYPTION")
    mx.y("QUOTA")
    mx.y("BLOCK")


def _ops_power(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s = p.sections
    pw = s["power"]
    mx.flag("WQ_POWER_EFFICIENT_DEFAULT", pw["wq_power_efficient"])
    mx.flag("ENERGY_MODEL", pw["energy_model"])
    mx.flag("SUSPEND", pw["suspend"])
    mx.flag("HIBERNATION", pw["hibernation"])
    if pw["hibernation"]:
        mx.choice(("HIBERNATION_COMP_LZO", "HIBERNATION_COMP_LZ4", "HIBERNATION_COMP_ZSTD"), "HIBERNATION_COMP_ZSTD", optional=True)
    mx.n("PM_AUTOSLEEP")
    mx.n("PM_WAKELOCKS")
    mx.n("PM_DEBUG")
    mx.n("PM_ADVANCED_DEBUG")
    mx.n("PM_TRACE_RTC")
    aspm = {"default": "PCIEASPM_DEFAULT", "powersave": "PCIEASPM_POWERSAVE", "powersupersave": "PCIEASPM_POWER_SUPERSAVE", "performance": "PCIEASPM_PERFORMANCE"}
    mx.choice(aspm.values(), aspm[pw["pcie_aspm"]])
    mx.val("SND_HDA_POWER_SAVE_DEFAULT", pw["hda_power_save"], optional=True)
    if d.facts.battery:
        mx.y("ACPI_BATTERY", optional=True)
        mx.y("ACPI_AC", optional=True)
    mx.y("POWERCAP")
    mx.y("CPU_THERMAL", optional=True)
    mx.y("THERMAL_GOV_STEP_WISE", optional=True)


def _ops_network(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    n = p.sections["network"]
    mx.y("TCP_CONG_ADVANCED")
    mx.y("TCP_CONG_CUBIC")
    mx.flag("TCP_CONG_BBR", True)
    mx.flag("TCP_CONG_RENO", n["congestion"] == "reno", optional=True)
    cong = {"bbr": "DEFAULT_BBR", "cubic": "DEFAULT_CUBIC", "reno": "DEFAULT_RENO"}
    mx.choice(cong.values(), cong[n["congestion"]], why=f"congestion={n['congestion']}")
    mx.y("NET_SCHED")
    mx.y("NET_SCH_FQ")
    mx.y("NET_SCH_FQ_CODEL")
    mx.flag("NET_SCH_CAKE", True)
    mx.flag("NET_SCH_FQ_PIE", n["qdisc"] == "fq_pie" or not p.lean("lean"))
    qd = {"fq": "DEFAULT_FQ", "fq_codel": "DEFAULT_FQ_CODEL", "fq_pie": "DEFAULT_FQ_PIE", "pfifo_fast": "DEFAULT_PFIFO_FAST", "cake": "DEFAULT_FQ_CODEL"}
    mx.y("NET_SCH_DEFAULT")
    mx.choice(("DEFAULT_FQ", "DEFAULT_CODEL", "DEFAULT_FQ_CODEL", "DEFAULT_FQ_PIE", "DEFAULT_SFQ", "DEFAULT_PFIFO_FAST"), qd[n["qdisc"]],
              why="cake is applied via sysctl at runtime" if n["qdisc"] == "cake" else "")
    mx.flag("MPTCP", n["mptcp"])
    mx.flag("MPTCP_IPV6", n["mptcp"])
    mx.flag("XDP_SOCKETS", n["xdp"])
    mx.flag("XDP_SOCKETS_DIAG", n["xdp"])
    mx.flag("NF_CONNTRACK_PROCFS", n["nf_conntrack_procfs"])
    mx.y("BQL")
    mx.y("NET_RX_BUSY_POLL")
    mx.y("CGROUP_NET_PRIO")
    mx.y("CGROUP_NET_CLASSID")
    mx.y("BPF_STREAM_PARSER", optional=True)
    mx.y("NET_FLOW_LIMIT")


def _ops_virt(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    f = d.facts
    if f.virt != "none":
        for sym in ("HYPERVISOR_GUEST", "PARAVIRT", "PARAVIRT_SPINLOCKS", "KVM_GUEST", "VIRTIO_MENU", "VIRTIO_PCI", "VIRTIO_BLK", "VIRTIO_NET", "VIRTIO_CONSOLE",
                    "VIRTIO_BALLOON", "VIRTIO_INPUT", "VIRTIO_FS", "VSOCKETS", "VIRTIO_VSOCKETS", "VIRTIO_MEM", "SCSI_VIRTIO", "HW_RANDOM_VIRTIO", "MEMORY_BALLOON",
                    "BALLOON_COMPACTION", "PAGE_REPORTING", "PTP_1588_CLOCK_KVM", "X86_HV_CALLBACK_VECTOR"):
            mx.y(sym, why=f"{f.virt} guest", optional=sym in ("PTP_1588_CLOCK_KVM", "X86_HV_CALLBACK_VECTOR", "VIRTIO_MEM"))
        if f.virt in ("microsoft", "hyperv"):
            for sym in ("HYPERV", "HYPERV_STORAGE", "HYPERV_NET", "HYPERV_BALLOON", "HYPERV_UTILS"):
                mx.m(sym)
        if f.virt == "vmware":
            for sym in ("VMWARE_VMCI", "VMWARE_BALLOON", "VMWARE_PVSCSI", "VMXNET3", "DRM_VMWGFX"):
                mx.m(sym)
        return
    if p.g("meta", "bare_metal_only"):
        for sym in ("HYPERVISOR_GUEST", "PARAVIRT", "KVM_GUEST", "XEN", "VIRTIO_MENU", "HYPERV", "VMWARE_VMCI", "VBOXGUEST"):
            mx.n(sym, why="bare_metal_only")


def _ops_gpu(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    f = d.facts
    gpus = set(f.gpus)
    if "amd" in gpus:
        mx.m("DRM_AMDGPU", why="AMD GPU present")
        mx.y("DRM_AMD_DC")
        mx.y("DRM_AMDGPU_SI", optional=True)
        mx.y("DRM_AMDGPU_CIK", optional=True)
        mx.y("DRM_AMDGPU_USERPTR", optional=True)
        mx.m("HSA_AMD", optional=True)
        mx.y("AMD_PRIVATE_COLOR", optional=True, why="enable AMD KMS color management for Gamescope/HDR")
    if "intel" in gpus:
        mx.m("DRM_I915", why="Intel GPU present")
        mx.m("DRM_XE", why="Intel GPU present")
        mx.y("DRM_XE_DISPLAY", optional=True)
    if "nvidia" in gpus and "nvidia" not in " ".join(f.dkms_modules):
        mx.m("DRM_NOUVEAU", why="NVIDIA GPU without nvidia-dkms")
    if gpus & {"virtio", "qxl", "bochs", "vmware"}:
        for sym in ("DRM_VIRTIO_GPU", "DRM_QXL", "DRM_BOCHS", "DRM_VMWGFX"):
            mx.m(sym, optional=True)
    mx.m("DRM", why="modular DRM core (mkinitcpio kms hook ships it in the initramfs)")
    mx.m("DRM_SIMPLEDRM", why="early firmware framebuffer console")
    mx.y("DRM_FBDEV_EMULATION")
    mx.y("DRM_PANIC", optional=True)
    if not d.rust or p.g("compiler", "lto") != "none":
        mx.n("DRM_PANIC_SCREEN_QR_CODE", optional=True, why="QR panic requires Rust; avoid LTO link mismatch")
        mx.s("DRM_PANIC_SCREEN", "kmsg", optional=True)


def _ops_extra(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    for sym, val in p.g("dusky", "extra_config").items():
        symbol = str(sym).removeprefix("CONFIG_")
        match val:
            case bool():
                mx.flag(symbol, val, why="dusky.extra_config")
            case int():
                mx.val(symbol, val, why="dusky.extra_config")
            case "m":
                mx.m(symbol, why="dusky.extra_config")
            case "y":
                mx.y(symbol, why="dusky.extra_config")
            case "n":
                mx.n(symbol, why="dusky.extra_config")
            case str():
                mx.s(symbol, val, why="dusky.extra_config")
    for sym in p.g("modules", "keep_symbols"):
        mx.m(str(sym).removeprefix("CONFIG_"), why="modules.keep_symbols")
    mx.s("LOCALVERSION", p.localversion())
    mx.n("LOCALVERSION_AUTO")
    mx.s("DEFAULT_HOSTNAME", "(none)")


def build_config_matrix(p: KernelProfile, d: Derived) -> Matrix:
    mx = Matrix(d.idx)
    for builder in (_ops_core, _ops_sched, _ops_cpu, _ops_timing, _ops_memory, _ops_compiler, _ops_security, _ops_gaming, _ops_storage,
                    _ops_power, _ops_network, _ops_virt, _ops_gpu, _ops_extra):
        builder(mx, p, d)
    return mx

# ---------------------------------------------------------------------------------------------------
# Apply / finalize / verify the configuration
# ---------------------------------------------------------------------------------------------------
def _op_args(op: Op) -> list[str]:
    match op.action:
        case "y":
            return ["-e", op.symbol]
        case "n":
            return ["-d", op.symbol]
        case "m":
            return ["-m", op.symbol]
        case "val":
            return ["--set-val", op.symbol, str(op.value)]
        case _:
            return ["--set-str", op.symbol, str(op.value)]


def apply_matrix(tree: Path, mx: Matrix) -> None:
    rule("Apply Kconfig matrix")
    cfg = tree / "scripts" / "config"
    ops = mx.ops
    for batch in itertools.batched(ops, 250):
        args = [arg for op in batch for arg in _op_args(op)]
        run([str(cfg), "--file", str(tree / ".config"), *args], cwd=tree)
    ok(f"Applied {len(ops)} Kconfig operations in {max(1, (len(ops) + 249) // 250)} batched scripts/config invocations")
    if mx.skipped:
        seen = sorted({op.symbol for op in mx.skipped})
        note(f"{len(seen)} symbols not offered by this tree were skipped: {', '.join(seen[:14])}{' ...' if len(seen) > 14 else ''}")


def finalize_config(tree: Path, env: Mapping[str, str]) -> None:
    rule("Resolve dependencies (olddefconfig)")
    run(["make", "olddefconfig"], cwd=tree, env=env)
    ok(".config resolved")


def parse_dotconfig(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("CONFIG_"):
            key, _, val = line[7:].partition("=")
            if val.startswith('"') and val.endswith('"') and len(val) >= 2:
                val = val[1:-1]
            out[key] = val
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            out[line[9:-11]] = "n"
    return out


VERIFY_HINTS: Final[dict[str, str]] = {
    "RUST": "Rust unavailable: see 'make LLVM=1 rustavailable'; RUST also requires !DEBUG_INFO_BTF || !LTO and !MODVERSIONS || GENDWARFKSYMS",
    "SCHED_BORE": "the BORE patch was not applied to this tree",
    "SCHED_ALT": "the Project C patch was not applied to this tree",
    "SCHED_CACHE": "cache-aware scheduling symbol missing or gated (needs SMP + SCHED_MC)",
    "PREEMPT_LAZY": "requires ARCH_HAS_PREEMPT_LAZY (x86-64 Linux >= 6.13)",
    "CFI": "kCFI needs clang -fsanitize=kcfi and is incompatible with GCC / FUNCTION_GRAPH_TRACER on some trees",
    "CFI_CLANG": "kCFI needs clang -fsanitize=kcfi",
    "AUTOFDO_CLANG": "requires clang >= 17 and CLANG_AUTOFDO_PROFILE",
    "PROPELLER_CLANG": "requires clang >= 19 and CLANG_PROPELLER_PROFILE_PREFIX",
    "X86_NATIVE_CPU": "tree lacks X86_NATIVE_CPU; KCFLAGS -march=native is used instead",
    "LD_DEAD_CODE_DATA_ELIMINATION": "x86-64 does not select HAS_LD_DEAD_CODE_DATA_ELIMINATION upstream (inert without out-of-tree patches)",
    "NTSYNC": "in-tree ntsync requires Linux >= 6.14 without BROKEN",
    "SLUB_CPU_PARTIAL": "unavailable under PREEMPT_RT or SLUB_TINY",
    "SLAB_BUCKETS": "depends on !SLUB_TINY",
    "DEBUG_INFO_BTF": "needs pahole >= 1.16 (>= 1.27 recommended) and non-reduced DWARF",
    "X86_64_VERSION": "depends on GENERIC_CPU and a compiler that knows x86-64-v levels",
    "ZRAM_MULTI_COMP": "requires ZRAM=y|m",
    "RCU_LAZY": "requires RCU_NOCB_CPU (RCU_EXPERT)",
    "PREEMPT_DYNAMIC": "requires HAVE_PREEMPT_DYNAMIC and !PREEMPT_RT",
    "HZ_500": "HZ choice injection into kernel/Kconfig.hz failed",
    "HZ_600": "HZ choice injection into kernel/Kconfig.hz failed",
    "HZ_750": "HZ choice injection into kernel/Kconfig.hz failed",
    "MPTCP_IPV6": "requires IPV6=y",
    "TRIM_UNUSED_KSYMS": "requires !COMPILE_TEST and MODULES",
    "PER_VMA_LOCK": "def_bool driven by ARCH_SUPPORTS_PER_VMA_LOCK && SMP",
    "INIT_STACK_ALL_ZERO": "requires a compiler supporting -ftrivial-auto-var-init=zero",
    "KFENCE_SAMPLE_INTERVAL": "only visible when KFENCE=y",
}


@dataclass(slots=True)
class VerifyReport:
    hard: list[tuple[Op, str | None]]
    soft: list[tuple[Op, str | None]]
    facts: list[str]

    @property
    def passed(self) -> bool:
        return not self.hard


def verify_config(tree: Path, p: KernelProfile, mx: Matrix, d: Derived) -> VerifyReport:
    rule("Verify .config contract")
    cfg_file = tree / ".config"
    if not cfg_file.is_file():
        raise VerifyError(".config does not exist after olddefconfig")
    cfg = parse_dotconfig(cfg_file.read_text(encoding="utf-8", errors="replace"))
    extra_soft = set(p.g("verify", "optional_symbols"))
    hard: list[tuple[Op, str | None]] = []
    soft: list[tuple[Op, str | None]] = []
    for op in mx.ops:
        actual = cfg.get(op.symbol)
        match op.action:
            case "y":
                good = actual in ("y", "m")
            case "m":
                good = actual in ("m", "y")
            case "n":
                good = actual in (None, "n")
            case "val":
                good = actual == str(op.value)
            case _:
                good = actual == str(op.value)
        if not good:
            (soft if op.optional or op.symbol in extra_soft else hard).append((op, actual))
    s = p.sections

    def require(cond: bool, sym: str, expect: str, msg: str) -> None:
        if cond and cfg.get(sym) not in expect.split("|"):
            hard.append((Op("y", sym, why=msg), cfg.get(sym)))

    require(s["verify"]["require_ntsync"] and s["gaming"]["ntsync"], "NTSYNC", "y|m", "verify.require_ntsync")
    require(s["verify"]["require_btf"] and d.scx_class and s["scheduler"]["scx_enable_class"], "DEBUG_INFO_BTF", "y", "verify.require_btf")
    require(s["verify"]["require_sched_ext"] and s["scheduler"]["scx"] != "none", "SCHED_CLASS_EXT", "y", "verify.require_sched_ext")
    require(True, "HZ", str(s["timing"]["hz"]), "timer frequency")
    require(True, "LOCALVERSION", p.localversion(), "LOCALVERSION")
    facts_out = [
        f"kernel {d.version} | LOCALVERSION {cfg.get('LOCALVERSION')} | HZ {cfg.get('HZ')} | preempt {'RT' if cfg.get('PREEMPT_RT') == 'y' else 'lazy' if cfg.get('PREEMPT_LAZY') == 'y' else 'full' if cfg.get('PREEMPT') == 'y' else 'voluntary/none'}"
        + (" +dynamic" if cfg.get("PREEMPT_DYNAMIC") == "y" else ""),
        f"scheduler {d.sched} | sched_ext {cfg.get('SCHED_CLASS_EXT', 'n')} | CAS {cfg.get('SCHED_CACHE', 'absent')} | BORE {cfg.get('SCHED_BORE', 'absent')}",
        f"toolchain {d.toolchain} | LTO {'full' if cfg.get('LTO_CLANG_FULL') == 'y' else 'thin' if cfg.get('LTO_CLANG_THIN') == 'y' else 'none'} | kCFI {cfg.get('CFI', cfg.get('CFI_CLANG', 'n'))} | Rust {cfg.get('RUST', 'n')} | BTF {cfg.get('DEBUG_INFO_BTF', 'n')} | AutoFDO {cfg.get('AUTOFDO_CLANG', 'n')}",
        f"memory: THP {'always' if cfg.get('TRANSPARENT_HUGEPAGE_ALWAYS') == 'y' else 'madvise' if cfg.get('TRANSPARENT_HUGEPAGE_MADVISE') == 'y' else 'never'} | SLUB_TINY {cfg.get('SLUB_TINY', 'n')} | MGLRU {cfg.get('LRU_GEN', 'n')} | ZRAM {cfg.get('ZRAM', 'n')} multi-comp {cfg.get('ZRAM_MULTI_COMP', 'n')} | ZSWAP {cfg.get('ZSWAP', 'n')} | DAMON {cfg.get('DAMON', 'n')} | LOG_BUF_SHIFT {cfg.get('LOG_BUF_SHIFT')} | NR_CPUS {cfg.get('NR_CPUS')}",
        f"cpu: native {cfg.get('X86_NATIVE_CPU', 'absent')} | X86_64_VERSION {cfg.get('X86_64_VERSION', 'absent')} | mitigations {cfg.get('CPU_MITIGATIONS', '?')} | amd_pstate mode {cfg.get('X86_AMD_PSTATE_DEFAULT_MODE', '?')} | NTSYNC {cfg.get('NTSYNC', 'n')}",
        f"modules: {sum(1 for v in cfg.values() if v == 'm')} =m, {sum(1 for v in cfg.values() if v == 'y')} =y | compress {'zstd' if cfg.get('MODULE_COMPRESS_ZSTD') == 'y' else 'other/none'} | TRIM_UNUSED_KSYMS {cfg.get('TRIM_UNUSED_KSYMS', 'n')}",
    ]
    for line in facts_out:
        note(line)
    if d.rust_reason:
        warn(f"Rust: {d.rust_reason}")
    if d.fdo_reason:
        warn(f"FDO: {d.fdo_reason}")
    if soft:
        note(f"{len(soft)} soft (dependency-gated) entries differ: " + ", ".join(f"{op.symbol}" for op, _ in soft[:12]) + (" ..." if len(soft) > 12 else ""))
    if hard:
        warn(f"{len(hard)} hard contract entries unmet:")
        for op, actual in hard[:30]:
            hint = VERIFY_HINTS.get(op.symbol) or op.why
            say(f"    {C.YELLOW}!{C.RESET} wanted {op.render():<48} got {actual if actual is not None else 'absent':<12} {C.DIM}{hint}{C.RESET}")
        if len(hard) > 30:
            say(f"    ... and {len(hard) - 30} more")
        if p.g("verify", "strict"):
            raise VerifyError(f"Verification contract failed ({len(hard)} hard entries). Fix the profile, add symbols to verify.optional_symbols, or set verify.strict=false.")
    else:
        ok(f"Contract satisfied: {len(mx)} operations verified against the resolved .config")
    return VerifyReport(hard, soft, facts_out)


def save_config_snapshot(tree: Path, p: KernelProfile) -> Path:
    CONFIG_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    dest = snapshot_path(p)
    (tree / ".config").copy(dest)
    ok(f"Config snapshot saved: {dest}")
    return dest


def kernelrelease(tree: Path, env: Mapping[str, str]) -> str:
    cp = run(["make", "-s", "kernelrelease"], cwd=tree, env=env, check=False)
    lines = [ln.strip() for ln in (cp.stdout or "").splitlines() if ln.strip() and not ln.startswith(("make", "scripts/"))]
    return lines[-1] if lines else ""


# ---------------------------------------------------------------------------------------------------
# ThinLTO persistent cache
# ---------------------------------------------------------------------------------------------------
def prune_thinlto_cache(cache: Path, limit_bytes: int) -> None:
    files = [f for f in cache.rglob("*") if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    if total <= limit_bytes:
        return
    files.sort(key=lambda f: f.stat().st_mtime)
    target = int(limit_bytes * 0.8)
    for f in files:
        if total <= target:
            break
        size = f.stat().st_size
        try:
            f.unlink()
            total -= size
        except OSError:
            pass
    info(f"ThinLTO cache pruned to {fmt_bytes(total)}")


def link_thinlto_cache(tree: Path, p: KernelProfile, d: Derived) -> None:
    link = tree / ".thinlto-cache"
    if d.lto != "thin" or not p.g("compiler", "thinlto_cache"):
        if link.is_symlink():
            link.unlink()
        return
    cache = THINLTO_CACHE_DIR / p.name
    cache.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        if link.resolve() == cache.resolve():
            pass
        else:
            link.unlink()
            link.symlink_to(cache, target_is_directory=True)
    else:
        if link.is_dir():
            shutil.rmtree(link)
        link.symlink_to(cache, target_is_directory=True)
    prune_thinlto_cache(cache, int(p.g("compiler", "thinlto_cache_size_gb")) << 30)
    ok(f"ThinLTO cache: {cache} (kbuild --thinlto-cache-dir=.thinlto-cache -> persistent symlink)")


# ---------------------------------------------------------------------------------------------------
# Build environment & compilation (make pacman-pkg -> linux-<flavor>{,-headers})
# ---------------------------------------------------------------------------------------------------
def needs_headers(facts: HostFacts) -> bool:
    return bool(facts.dkms_modules) or bool(facts.tools.get("dkms"))


def resolve_build_headers(p: KernelProfile, facts: HostFacts) -> bool:
    match p.g("compiler", "headers"):
        case "always":
            return True
        case "never":
            return False
        case _:
            return needs_headers(facts)


def build_env(p: KernelProfile, d: Derived, facts: HostFacts, epoch: float) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("LOCALVERSION", "MAKEFLAGS", "KCFLAGS", "KRUSTFLAGS", "LLVM", "LLVM_IAS", "CC", "LD", "AR", "NM", "OBJCOPY", "STRIP", "HOSTCC", "HOSTLD"):
        env.pop(key, None)
    env["LANG"] = env["LC_ALL"] = "C.UTF-8"
    kbuild_user = (p.g("dusky", "user") or "").strip() or os.environ.get("KBUILD_BUILD_USER") or os.environ.get("USER") or "builduser"
    kbuild_host = (p.g("dusky", "hostname") or "").strip() or os.environ.get("KBUILD_BUILD_HOST") or platform.node() or "archlinux"
    env["KBUILD_BUILD_USER"] = kbuild_user
    env["KBUILD_BUILD_HOST"] = kbuild_host
    if p.g("dusky", "reproducible"):
        env["KBUILD_BUILD_TIMESTAMP"] = datetime.fromtimestamp(epoch, UTC).strftime("%a %b %d %H:%M:%S UTC %Y")
        env["SOURCE_DATE_EPOCH"] = str(int(epoch))
    if d.toolchain == "llvm":
        env["LLVM"] = "1"
        env["LLVM_IAS"] = "1"
    else:
        env["CC"] = "gcc"
        env["HOSTCC"] = "gcc"
        env["LD"] = "ld.bfd"
    kcflags = list(d.kcflags)
    if p.g("compiler", "optimize") == "o3":
        kcflags.append("-O3")
    kcflags += shlex.split(p.g("cpu", "march"))
    if kcflags:
        env["KCFLAGS"] = " ".join(kcflags)
    if d.rust and d.krustflags:
        env["KRUSTFLAGS"] = " ".join(d.krustflags)
    if d.fdo != "none":
        pdir = Path(p.g("compiler", "fdo_profile_dir")).expanduser() if p.g("compiler", "fdo_profile_dir") else STATE_DIR / "fdo" / p.name
        env["CLANG_AUTOFDO_PROFILE"] = str(pdir / "kernel.afdo")
        if d.fdo == "autofdo_propeller":
            env["CLANG_PROPELLER_PROFILE_PREFIX"] = str(pdir / "propeller")
    jobs = p.g("compiler", "jobs") or auto_jobs(facts, d.lto)
    env["MAKEFLAGS"] = f"-j{jobs}"
    env["KCONFIG_NOTIMESTAMP"] = "1"
    env["ZSTD_CLEVEL"] = "9"
    return env


def check_disk_space(lto: str) -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(BUILD_DIR).free
    need = (30 if lto == "full" else 22) << 30
    if free < (8 << 30):
        raise BuildError(f"Only {fmt_bytes(free)} free in {BUILD_DIR}; a kernel build needs >= {fmt_bytes(need)}")
    if free < need:
        warn(f"{fmt_bytes(free)} free in {BUILD_DIR}; builds with debug info can exceed {fmt_bytes(need)}")


def check_dependencies(p: KernelProfile, facts: HostFacts, d_toolchain: str, want_rust: bool) -> None:
    if os.geteuid() == 0:
        raise DependencyError("makepkg refuses to run as root; run this tool as a regular user (sudo is requested only for installation)")
    reqs = [("make", "base-devel"), ("makepkg", "pacman"), ("fakeroot", "base-devel"), ("bc", "bc"), ("tar", "tar"), ("xz", "xz"), ("zstd", "zstd"), ("kmod", "kmod"),
            ("cpio", "cpio"), ("perl", "perl"), ("flex", "flex"), ("bison", "bison"), ("patch", "patch"), ("pahole", "pahole"), ("python3", "python")]
    if d_toolchain == "llvm":
        reqs += [("clang", "clang"), ("ld.lld", "lld"), ("llvm-ar", "llvm"), ("llvm-objcopy", "llvm")]
    else:
        reqs += [("gcc", "gcc"), ("ld.bfd", "binutils")]
    if want_rust:
        reqs += [("rustc", "rust"), ("bindgen", "rust-bindgen")]
    if not have("curl") and not have("aria2c"):
        reqs.append(("curl", "curl"))
    missing = [(cmd, pkg) for cmd, pkg in reqs if not have(cmd)]
    if missing:
        pkgs = sorted({pkg for _, pkg in missing})
        warn("Missing tools: " + ", ".join(cmd for cmd, _ in missing))
        if interactive() and ask_yes(f"Install now with pacman -S --needed {' '.join(pkgs)}?", True):
            PRIV.run(["pacman", "-S", "--needed", "--noconfirm", *pkgs], capture=False)
            missing = [(cmd, pkg) for cmd, pkg in missing if not have(cmd)]
        if missing:
            raise DependencyError("Install: pacman -S --needed " + " ".join(pkgs))
    if p.g("compiler", "polly") and d_toolchain == "llvm":
        polly_found = any(Path(loc).is_file() for loc in [
            "/usr/lib/LLVMPolly.so",
            "/usr/lib/llvm/lib/LLVMPolly.so",
            *Path("/usr/lib").glob("llvm*/lib/LLVMPolly.so"),
        ])
        if not polly_found:
            warn("Clang Polly optimization is enabled, but LLVMPolly.so was not found (Arch package: polly)")
            if interactive() and ask_yes("Install polly now with pacman -S --needed polly?", True):
                PRIV.run(["pacman", "-S", "--needed", "--noconfirm", "polly"], capture=False)
                polly_found = any(Path(loc).is_file() for loc in [
                    "/usr/lib/LLVMPolly.so",
                    "/usr/lib/llvm/lib/LLVMPolly.so",
                    *Path("/usr/lib").glob("llvm*/lib/LLVMPolly.so"),
                ])
            if not polly_found:
                raise DependencyError("Clang Polly requires the 'polly' package: sudo pacman -S --needed polly")
    clang_v = facts.tools.get("clang", "")
    if d_toolchain == "llvm" and clang_v and version_tuple(clang_v) < (19,):
        warn(f"clang {clang_v} detected; Linux 7.x LTO/kCFI/AutoFDO paths are validated with clang >= 21")
    pahole_v = facts.tools.get("pahole", "")
    if pahole_v and version_tuple(pahole_v) < (1, 27):
        warn(f"pahole {pahole_v} is old; BTF for Rust/LTO kernels needs >= 1.27")


def rust_probe(tree: Path, env: Mapping[str, str]) -> tuple[bool, str]:
    cp = run(["make", "rustavailable"], cwd=tree, env=env, check=False, timeout=300)
    return cp.returncode == 0, cp.stdout or ""


def compile_kernel(tree: Path, p: KernelProfile, d: Derived, env: Mapping[str, str], facts: HostFacts) -> list[Path]:
    rule("Compile kernel & build pacman packages")
    jobs = p.g("compiler", "jobs") or auto_jobs(facts, d.lto)
    pkgdest = PKGDEST_DIR / p.name
    pkgdest.mkdir(parents=True, exist_ok=True)
    b_env = dict(env)
    b_env["PACMAN_PKGBASE"] = p.pkgbase
    b_env["PKGDEST"] = str(pkgdest)
    b_env["PACKAGER"] = f"{APP_NAME} <dusky@localhost>"
    b_env["PACMAN_EXTRAPACKAGES"] = "headers" if resolve_build_headers(p, facts) else ""
    b_env["MAKEFLAGS"] = f"-j{jobs}"
    b_env["ZSTD_CLEVEL"] = "9"
    info(f"pkgbase={p.pkgbase} jobs={jobs} lto={d.lto} toolchain={d.toolchain} headers={'yes' if b_env['PACMAN_EXTRAPACKAGES'] else 'no'} rust={'yes' if d.rust else 'no'}")
    if d.lto == "full":
        warn("Full LTO: the final vmlinux link is single-threaded and memory hungry; expect a long silent phase")
    start_wall = time.time()
    expected_steps, expected_seconds = history_estimate(p.name, d.lto)
    with Live(p.pkgbase, expected_steps, expected_seconds) as live:
        ret = run_stream(["make", f"-j{jobs}", "pacman-pkg"], cwd=tree, env=b_env, on_line=live.feed)
        duration = live.elapsed
        steps = live.steps
        errors = list(live.errors)
        tail = list(live.tail)
    record_history({"profile": p.name, "version": d.version, "lto": d.lto, "toolchain": d.toolchain, "jobs": jobs, "duration": round(duration, 1),
                    "steps": steps, "success": ret == 0, "ts": datetime.now(UTC).isoformat()})
    if ret != 0:
        err(f"Kernel build failed (exit {ret}) after {fmt_duration(duration)}")
        for line in (errors or tail)[-20:]:
            say(f"    {C.RED}{line}{C.RESET}")
        raise BuildError(f"make pacman-pkg failed (exit {ret}); full log: {JOURNAL.path}")
    pkgs = sorted((f for f in pkgdest.glob(f"{p.pkgbase}*.pkg.tar*") if f.stat().st_mtime >= start_wall - 5), key=lambda f: f.name)
    if not pkgs:
        raise BuildError(f"No packages produced in {pkgdest}")
    ok(f"Built in {fmt_duration(duration)} ({steps:,} kbuild steps):")
    for f in pkgs:
        say(f"    {C.GREEN}•{C.RESET} {f.name} ({fmt_bytes(f.stat().st_size)})")
    return pkgs


def install_packages(pkgs: Sequence[Path]) -> None:
    rule("Install packages (pacman -U)")
    PRIV.ensure()
    PRIV.run(["pacman", "-U", "--noconfirm", *[str(x) for x in pkgs]], capture=False)
    ok("Kernel packages installed (mkinitcpio and DKMS pacman hooks have run)")

# ---------------------------------------------------------------------------------------------------
# Bootloader integration
# ---------------------------------------------------------------------------------------------------
def base_cmdline_tokens(facts: HostFacts) -> list[str]:
    out: list[str] = []
    for tok in shlex.split(facts.cmdline):
        key = tok.split("=", 1)[0]
        if key in ("BOOT_IMAGE", "initrd", "initrdefi") or key in MANAGED_CMDLINE_KEYS:
            continue
        out.append(tok)
    return out


def uki_preset(pkgbase: str) -> bool:
    preset = _read(f"/etc/mkinitcpio.d/{pkgbase}.preset")
    return any(re.match(r"^\s*\w+_uki=", line) for line in preset.splitlines())


def write_bls_entries(p: KernelProfile, facts: HostFacts, d: Derived) -> None:
    if "systemd-boot" not in facts.bootloaders or not p.g("boot", "write_entries"):
        return
    if uki_preset(p.pkgbase):
        note("mkinitcpio builds a UKI for this flavor; systemd-boot discovers it automatically (no entry written)")
        return
    root = facts.xbootldr or facts.esp
    has_loader = Path(root, "loader").is_dir() if root else False
    if not has_loader and root:
        has_loader = PRIV.run(["test", "-d", f"{root}/loader"], check=False).returncode == 0
    if not root or not has_loader:
        warn("systemd-boot detected but no loader/ directory found on ESP/XBOOTLDR; skipping entries")
        return
    if Path(root).resolve() != Path("/boot").resolve():
        warn(f"Kernel images live in /boot but systemd-boot reads {root}; switch mkinitcpio to UKI or mount XBOOTLDR at /boot")
        return
    params = base_cmdline_tokens(facts)
    if p.g("boot", "cmdline") == "entry":
        params += flavor_cmdline(p, facts)
    ucode: list[str] = []
    if not facts.microcode_hook:
        for img in ("intel-ucode.img", "amd-ucode.img"):
            if Path("/boot", img).is_file():
                ucode.append(f"initrd  /{img}")
    entries_dir = Path(root) / "loader" / "entries"
    files: dict[Path, tuple[str, str]] = {}
    for suffix, title in (("", ""), ("-fallback", " (fallback initramfs)")):
        body = [f"title   Arch Linux ({p.pkgbase}){title}", f"version {d.kernelrelease or d.version}", f"sort-key dusky-{p.suffix}", f"linux   /vmlinuz-{p.pkgbase}",
                *ucode, f"initrd  /initramfs-{p.pkgbase}{suffix}.img", "options " + " ".join(params)]
        files[entries_dir / f"{p.pkgbase}{suffix}.conf"] = ("\n".join(body) + "\n", "0644")
    PRIV.write_files(files)
    ok(f"systemd-boot entries written: {', '.join(f.name for f in files)}")
    if have("bootctl"):
        PRIV.run(["bootctl", "set-default", f"{p.pkgbase}.conf"], check=False)
        ok(f"systemd-boot default entry set to {p.pkgbase}.conf")


def refresh_boot(p: KernelProfile, facts: HostFacts, d: Derived, *, kernel_install: bool) -> None:
    rule("Bootloader refresh")
    PRIV.ensure()
    if "grub" in facts.bootloaders and have("grub-mkconfig"):
        PRIV.run(["grub-mkconfig", "-o", "/boot/grub/grub.cfg"], check=False, capture=False)
        ok("GRUB configuration regenerated")
    if "refind" in facts.bootloaders:
        if not Path("/boot/refind_linux.conf").is_file() and have("mkrlconf"):
            PRIV.run(["mkrlconf"], check=False)
        ok("rEFInd auto-detects /boot/vmlinuz-* (options in /boot/refind_linux.conf)")
    if "limine" in facts.bootloaders and have("limine-update"):
        PRIV.run(["limine-update"], check=False, capture=False)
        ok("Limine entries updated")
    write_bls_entries(p, facts, d)
    if kernel_install and have("kernel-install") and d.kernelrelease:
        PRIV.run(["kernel-install", "add", d.kernelrelease, f"/usr/lib/modules/{d.kernelrelease}/vmlinuz"], check=False, capture=False)
    params = flavor_cmdline(p, facts)
    mode = p.g("boot", "cmdline")
    if mode == "bake":
        note("Flavor parameters are baked into CONFIG_CMDLINE (bootloader options still override): " + " ".join(params))
    else:
        info("Recommended kernel parameters for this flavor: " + " ".join(params))
    if not facts.bootloaders:
        warn("No supported bootloader detected (systemd-boot, GRUB, rEFInd, Limine); add /boot/vmlinuz-" + p.pkgbase + " manually")

# ---------------------------------------------------------------------------------------------------
# Cross-machine hardware bundles
# ---------------------------------------------------------------------------------------------------
def do_export_bundle(dest: Path | None) -> Path:
    banner()
    rule("Export hardware bundle")
    facts = host_facts()
    hname = re.sub(r"[^a-z0-9]+", "_", platform.node().lower()).strip("_") or "machine"
    out = dest or (Path.home() / f"dusky_bundle_{hname}.tar.gz")
    out.parent.mkdir(parents=True, exist_ok=True)
    if have("modprobed-db"):
        run(["modprobed-db", "store"], check=False, timeout=60)
    manifest = {"format": "dusky_bundle_v2", "hostname": hname, "created_at": datetime.now(UTC).isoformat(), "app_version": APP_VERSION,
                "uarch": facts.uarch, "psabi_level": facts.psabi_level, "vendor": facts.vendor, "model": facts.model, "threads": facts.threads,
                "mem_gib": round(facts.mem_gib, 2), "gpus": list(facts.gpus), "filesystems": list(facts.filesystems), "root_fs": facts.root_fs,
                "root_luks": facts.root_luks, "has_nvme": facts.has_nvme, "rotational": facts.rotational, "virt": facts.virt, "battery": facts.battery,
                "dkms_modules": list(facts.dkms_modules), "bootloaders": list(facts.bootloaders), "initrd_compression": facts.initrd_compression,
                "kernel": facts.kernel, "cmdline": facts.cmdline, "flags": sorted(facts.flags)}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if MODPROBED_DB_PATH.is_file():
            MODPROBED_DB_PATH.copy(tmp / "modprobed.db")
        for src in (Path("/proc/cpuinfo"), Path("/proc/meminfo"), Path("/proc/cmdline")):
            (tmp / src.name).write_text(_read(src), encoding="utf-8")
        if have("lspci"):
            (tmp / "lspci.txt").write_text(run(["lspci", "-nn"], check=False).stdout or "", encoding="utf-8")
        if have("lsmod"):
            (tmp / "lsmod.txt").write_text(run(["lsmod"], check=False).stdout or "", encoding="utf-8")
        with tarfile.open(out, "w:gz") as tar:
            for f in sorted(tmp.iterdir()):
                tar.add(f, arcname=f.name)
    ok(f"Exported {out} ({fmt_bytes(out.stat().st_size)}) -- uarch {facts.uarch or 'generic_v' + str(facts.psabi_level)}, {facts.threads} threads, {facts.mem_gib:.1f} GiB")
    return out


def do_import_bundle(src: Path) -> str:
    banner()
    rule("Import hardware bundle")
    if not src.is_file():
        raise DuskyError(f"Bundle not found: {src}")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        with tarfile.open(src, "r:*") as tar:
            tar.extractall(tmp, filter="data")
        mf = tmp / "manifest.json"
        if not mf.is_file():
            raise DuskyError("Invalid bundle: manifest.json missing")
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        hname = re.sub(r"[^a-z0-9]+", "_", str(manifest.get("hostname", "remote"))).strip("_") or "remote"
        import_dir = IMPORT_DIR / hname
        import_dir.mkdir(parents=True, exist_ok=True)
        db_path = ""
        if (tmp / "modprobed.db").is_file():
            (tmp / "modprobed.db").copy(import_dir / "modprobed.db")
            db_path = str(import_dir / "modprobed.db")
        elif (tmp / "lsmod.txt").is_file():
            (tmp / "lsmod.txt").copy(import_dir / "modprobed.db")
            db_path = str(import_dir / "modprobed.db")
        (tmp / "manifest.json").copy(import_dir / "manifest.json")
    arch = manifest.get("uarch") or f"generic_v{int(manifest.get('psabi_level', 3))}"
    if arch not in CPU_ARCHES or arch == "native":
        arch = f"generic_v{min(3, int(manifest.get('psabi_level', 3)))}"
    mem = float(manifest.get("mem_gib", 16))
    tweaks: dict[str, dict[str, Any]] = {
        "meta": {"portable_package": True, "bare_metal_only": False, "tags": ["remote", hname]},
        "release": {"channel": "stable"},
        "scheduler": {"type": "eevdf", "scx": "scx_bpfland" if mem > 6 else "none", "scx_flags": "", "scx_enable_class": mem > 6},
        "cpu": {"arch": arch, "nr_cpus": int(manifest.get("threads", 8)), "amd_pstate": "active" if manifest.get("vendor") == "amd" else "undefined", "mitigations": "on"},
        "memory": {"footprint": suggest_footprint(mem), "swap_backend": "zram", "page_reporting": manifest.get("virt", "none") != "none"},
        "compiler": {"toolchain": "llvm", "lto": "thin", "headers": "always" if manifest.get("dkms_modules") else "never", "rust": False},
        "storage": {"extra_filesystems": [fs for fs in manifest.get("filesystems", []) if fs in FS_SYMBOLS]},
        "modules": {"mode": "strict" if db_path else "expanded", "modprobed_db": bool(db_path), "modprobed_db_path": db_path},
        "security": {"profile": "balanced"},
        "boot": {"write_entries": False},
    }
    pname = f"remote_{hname}"
    prof = profile_from_tweaks(pname, f"Remote bundle for {hname}: {manifest.get('model', 'unknown CPU')}, {mem:.0f} GiB, {', '.join(manifest.get('gpus', [])) or 'no GPU info'}",
                               f"dusky-{hname.replace('_', '-')}"[:40], tweaks, priority=90)
    dest_dir = PROFILES_DIR if os.access(PROFILES_DIR, os.W_OK) or not PROFILES_DIR.exists() else USER_PROFILES_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / f"{pname}.toml").write_text(render_profile_toml(prof.sections, header=f"imported from {src.name}"), encoding="utf-8")
    ok(f"Registered profile '{pname}' ({dest_dir / (pname + '.toml')}); build with: --profile {pname} --no-install")
    return pname


# ---------------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------------
def toolchain_env(p: KernelProfile) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("LOCALVERSION", "MAKEFLAGS", "KCFLAGS", "KRUSTFLAGS", "LLVM", "LLVM_IAS", "CC", "LD"):
        env.pop(key, None)
    env["LANG"] = env["LC_ALL"] = "C.UTF-8"
    env["KCONFIG_NOTIMESTAMP"] = "1"
    if p.g("compiler", "toolchain") == "llvm":
        env["LLVM"] = "1"
        env["LLVM_IAS"] = "1"
    else:
        env["CC"] = "gcc"
        env["HOSTCC"] = "gcc"
    return env


def show_configuration(profile: KernelProfile, diff: Sequence[str]) -> None:
    rule(f"Profile: {profile.name}")
    if profile.description:
        say(f"  {C.DIM}{profile.description}{C.RESET}")
    table(["setting", "value"], [[k, v] for k, v in profile.summarize()])
    if diff:
        say("")
        say(f"  {C.YELLOW}ephemeral overrides / auto-adjustments:{C.RESET}")
        for line in diff:
            say(f"    {C.YELLOW}{line}{C.RESET}")


def do_build(args: argparse.Namespace) -> int:
    banner()
    facts = host_facts()
    profiles = ensure_profiles_exist()
    profile = select_profile(profiles, args.profile, facts).clone()
    diff = configure_profile_interactively(profile, facts, args)

    if getattr(args, "build_dir", None):
        set_build_dir(args.build_dir)
    elif interactive() and not ASSUME_YES and not getattr(args, "no_prompt", False):
        chosen_dir = ask("Build directory (RAM/ZRAM disk recommended for speed)", str(BUILD_DIR))
        set_build_dir(chosen_dir)
    else:
        set_build_dir(BUILD_DIR)

    while True:
        show_configuration(profile, diff)
        if getattr(args, "no_prompt", False) or ASSUME_YES or not interactive():
            break
        action = ask("Proceed with this configuration? [Y]es / [e]dit / [n]o", "y").strip().lower()
        if action in ("y", "yes"):
            break
        if action in ("n", "no", "q", "quit"):
            info("Aborted by user")
            return 0
        if action in ("e", "edit", "m", "menu"):
            diff.extend(run_wizard(profile, facts))
            diff = wizard_review_loop(profile, facts, diff, force=bool(getattr(args, "force", False)))
            if diff:
                offer_save_profile(profile)
            continue
        warn(f"Unrecognized response '{action}'. Choose [y]es to proceed, [e]dit to modify, or [n]o to abort.")

    if not args.no_install and not args.configure_only:
        PRIV.ensure()
    JOURNAL.open(profile.name)
    note(f"journal: {JOURNAL.path}")
    check_dependencies(profile, facts, profile.g("compiler", "toolchain"), bool(profile.g("compiler", "rust")))
    check_disk_space(profile.g("compiler", "lto"))
    rule("Kernel release")
    release = choose_release(profile, fetch_releases())
    tarball = obtain_tarball(release, bool(profile.g("release", "require_signature")))
    patchset = profile.g("scheduler", "type") if profile.g("scheduler", "type") != "eevdf" else ""
    tree = unpack(tarball, release, patchset, bool(args.fresh))
    sched = apply_scheduler_patch(tree, profile, release)
    apply_enhancement_patches(tree, profile, release, facts)
    ensure_hz_choice(tree, int(profile.g("timing", "hz")))
    env0 = toolchain_env(profile)
    seed_source = seed_config(tree, profile, env0, Path(args.seed_config).expanduser() if args.seed_config else None)
    run(["make", "olddefconfig"], cwd=tree, env=env0)
    db = ensure_modprobed_db(profile)
    localmodconfig(tree, profile, db, env0)
    idx = KconfigIndex.scan(tree)
    note(f"Kconfig index: {len(idx.symbols):,} symbols (x86 view), X86_64_VERSION range max {idx.x86_64_version_max}")
    rust_ok, rust_out = (rust_probe(tree, env0) if profile.g("compiler", "rust") else (False, ""))
    d = derive(profile, facts, idx, tree, sched, rust_ok, rust_out)
    d.seed_source = seed_source
    inject_dkms_march_in_makefile(tree, d.march, d.mtune)
    mx = build_config_matrix(profile, d)
    apply_matrix(tree, mx)
    env = build_env(profile, d, facts, tarball.stat().st_mtime)
    finalize_config(tree, env)
    d.kernelrelease = kernelrelease(tree, env)
    info(f"kernelrelease: {d.kernelrelease}")
    verify_config(tree, profile, mx, d)
    save_config_snapshot(tree, profile)
    if args.print_matrix:
        rule("Kconfig matrix")
        for op in mx.ops:
            say(f"  {op.render():<56} {C.DIM}{op.why}{C.RESET}")
    if args.configure_only:
        ok(f"Configuration complete (--configure-only). Tree: {tree}")
        return 0
    link_thinlto_cache(tree, profile, d)
    pkgs = compile_kernel(tree, profile, d, env, facts)
    if args.no_install:
        ok("Packages built (--no-install). Install later with: sudo pacman -U " + " ".join(str(x) for x in pkgs))
        return 0
    install_packages(pkgs)
    refresh_boot(profile, facts, d, kernel_install=bool(args.kernel_install))
    rule("Done")
    ok(f"{d.kernelrelease} ({profile.name}) installed as {profile.pkgbase}. Reboot to test; roll back with --uninstall {profile.suffix}.")
    send_notification("Kernel build complete", f"{d.kernelrelease} ({profile.name}) installed", icon="dialog-information")
    return 0


def do_list(_: argparse.Namespace) -> int:
    print_profile_table(ensure_profiles_exist(), host_facts())
    return 0


def do_show(args: argparse.Namespace) -> int:
    p = select_profile(ensure_profiles_exist(), args.profile, host_facts()).clone()
    diff = apply_overrides(p, Overrides.from_env_and_args(args)) + normalize_profile(p)
    if args.json:
        say(json.dumps({"profile": p.name, "sections": p.sections, "diff": diff}, indent=2))
        return 0
    if args.dump_toml:
        sys.stdout.write(render_profile_toml(p.sections, header=f"resolved view of {p.name}"))
        return 0
    show_configuration(p, diff)
    return 0


def do_spec(_: argparse.Namespace) -> int:
    for sec, fields in PROFILE_SPEC.items():
        say(f"{C.BOLD}[{sec}]{C.RESET}")
        for f in fields:
            choices = f" choices={'|'.join(map(str, f.choices))}" if f.choices else ""
            rng = f" range={f.minimum}..{f.maximum}" if f.kind == "int" and (f.minimum is not None or f.maximum is not None) else ""
            say(f"  {f.key:<26} {f.kind:<5} default={toml_scalar(f.default):<28} {f.help}{choices}{rng}")
        say("")
    return 0


def latest_tree() -> Path | None:
    if not SRC_DIR.is_dir():
        return None
    trees = [d for d in SRC_DIR.iterdir() if d.is_dir() and is_valid_kernel_tree(d)]
    return max(trees, key=lambda d: d.stat().st_mtime) if trees else None


def do_matrix(args: argparse.Namespace) -> int:
    banner()
    facts = host_facts()
    p = select_profile(ensure_profiles_exist(), args.profile, facts).clone()
    diff = apply_overrides(p, Overrides.from_env_and_args(args)) + normalize_profile(p)
    validate_profile(p)
    tree = latest_tree()
    if tree is not None:
        idx = KconfigIndex.scan(tree)
        note(f"Using Kconfig index of {tree.name} ({len(idx.symbols):,} symbols)")
    else:
        idx = KconfigIndex(frozenset(), 3)
        note("No extracted source tree yet; showing the permissive matrix (every symbol assumed available)")
    d = derive(p, facts, idx, tree or Path("."), p.g("scheduler", "type"), True, "")
    mx = build_config_matrix(p, d)
    show_configuration(p, diff)
    rule(f"Kconfig matrix ({len(mx)} ops)")
    for op in mx.ops:
        flag = " (soft)" if op.optional else ""
        say(f"  {op.render():<56} {C.DIM}{op.why}{flag}{C.RESET}")
    if mx.skipped:
        note(f"skipped (absent in tree): {', '.join(sorted({o.symbol for o in mx.skipped}))}")
    rule("Flavor command line")
    say("  " + " ".join(flavor_cmdline(p, facts)))
    return 0


def do_doctor(args: argparse.Namespace) -> int:
    facts = host_facts()
    if args.json:
        say(json.dumps({"facts": facts.as_json(), "python": sys.version.split()[0], "paths": {"build": str(BUILD_DIR), "profiles": [str(d) for d in profile_dirs()],
                        "snapshots": str(CONFIG_SNAPSHOT_DIR), "logs": str(LOG_DIR)}}, indent=2))
        return 0
    banner()
    rule("Host")
    table(["item", "value"], [
        ["python", sys.version.split()[0]], ["running kernel", facts.kernel], ["cpu", f"{facts.model} ({facts.vendor}, {facts.cores}c/{facts.threads}t)"],
        ["uarch / psABI", f"{facts.uarch or 'unknown'} / x86-64-v{facts.psabi_level}"], ["LLC", f"{facts.llc_domains} domain(s), {facts.llc_kib // 1024} MiB"],
        ["memory", f"{facts.mem_gib:.1f} GiB RAM, {facts.swap_gib:.1f} GiB swap ({'disk swap present' if facts.disk_swap else 'no disk swap'})"],
        ["suggested footprint", suggest_footprint(facts.mem_gib)], ["virtualization", facts.virt], ["gpus", ", ".join(facts.gpus) or "none"],
        ["root fs", f"{facts.root_fs}{' on dm-crypt' if facts.root_luks else ''}; all: {', '.join(facts.filesystems)}"],
        ["storage", f"nvme={'yes' if facts.has_nvme else 'no'} rotational={'yes' if facts.rotational else 'no'}"], ["battery", "yes" if facts.battery else "no"],
        ["bootloaders", ", ".join(facts.bootloaders) or "none detected"], ["ESP / XBOOTLDR", f"{facts.esp or '-'} / {facts.xbootldr or '-'}"],
        ["mkinitcpio", f"compression={facts.initrd_compression} microcode_hook={'yes' if facts.microcode_hook else 'no'}"],
        ["DKMS modules", ", ".join(facts.dkms_modules) or "none"], ["sched_ext live", "yes" if facts.sched_ext_live else "no"],
        ["CAS knob", "present" if any(Path(f"/sys/kernel/debug/sched/{k}").exists() for k in ("llc_aggr_tolerance", "cache_aggr_tolerance")) else "absent/debugfs not mounted"],
        ["amd_pstate", _read("/sys/devices/system/cpu/amd_pstate/status").strip() or "n/a"], ["THP", _read("/sys/kernel/mm/transparent_hugepage/enabled").strip() or "n/a"],
        ["zram", ", ".join(p.name for p in Path("/sys/block").glob("zram*")) or "none"],
    ])
    rule("Toolchain & Systems Integration")
    rows = []
    # (tool, category, is_required)
    tool_defs = [
        ("clang", "compiler (LLVM)", True),
        ("ld.lld", "linker (LLD)", True),
        ("llvm-ar", "archiver (LLVM)", True),
        ("rustc", "Rust-for-Linux", True),
        ("bindgen", "Rust-for-Linux", True),
        ("pahole", "BTF generation", True),
        ("make", "build automation", True),
        ("makepkg", "Arch packaging", True),
        ("mkinitcpio", "initramfs generator", True),
        ("modprobed-db", "hardware module profiler", True),
        ("zram-generator", "ZRAM swap generator (optional)", False),
        ("perf", "kernel telemetry / AutoFDO", False),
        ("scx_lavd", "sched_ext gaming/latency", False),
        ("scx_bpfland", "sched_ext low-latency", False),
        ("dkms", "out-of-tree dynamic modules", False),
        ("bootctl", "systemd-boot management", "systemd-boot" in facts.bootloaders),
        ("kernel-install", "kernel install framework", False),
        ("grub-mkconfig", "GRUB bootloader", "grub" in facts.bootloaders),
        ("limine-update", "Limine bootloader", "limine" in facts.bootloaders),
        ("create_llvm_prof", "Google AutoFDO (optional)", False),
        ("aria2c", "multi-stream downloader (optional)", False),
    ]
    for name, cat, required in tool_defs:
        v = facts.tools.get(name, "")
        if v:
            status = f"{C.GREEN}{v}{C.RESET}"
        elif required:
            status = f"{C.RED}missing (required){C.RESET}"
        else:
            status = f"{C.DIM}not installed (optional){C.RESET}"
        rows.append([name, cat, status])
    table(["tool", "subsystem", "status"], rows)
    rule("Paths")
    free = shutil.disk_usage(BUILD_DIR if BUILD_DIR.exists() else Path.home()).free
    table(["path", "value"], [["profiles", ", ".join(str(d) for d in profile_dirs())], ["build dir", f"{BUILD_DIR} ({fmt_bytes(free)} free)"],
                              ["snapshots", str(CONFIG_SNAPSHOT_DIR)], ["ThinLTO cache", str(THINLTO_CACHE_DIR)], ["packages", str(PKGDEST_DIR)], ["logs", str(LOG_DIR)],
                              ["modprobed.db", f"{MODPROBED_DB_PATH} ({'present' if MODPROBED_DB_PATH.is_file() else 'missing'})"]])
    hist = load_history()
    if hist:
        rule("Recent builds")
        table(["when", "profile", "version", "lto", "duration", "result"],
              [[h.get("ts", "")[:16], h.get("profile"), h.get("version"), h.get("lto"), fmt_duration(float(h.get("duration", 0))), "ok" if h.get("success") else "failed"] for h in hist[-8:]])
    if facts.mem_gib <= 8.5:
        info(f"{facts.mem_gib:.1f} GiB host: profiles low_ram / minimal_strict / embedded_lowram target this class of machine")
    return 0


def do_clean(args: argparse.Namespace) -> int:
    banner()
    rule("Clean artifacts")
    what = args.clean or "all"
    targets = {"src": SRC_DIR, "tarballs": TARBALL_DIR, "patches": PATCH_CACHE, "packages": PKGDEST_DIR, "thinlto": THINLTO_CACHE_DIR, "logs": LOG_DIR, "seeds": BUILD_DIR / "seeds"}
    chosen = list(targets) if what == "all" else [w.strip() for w in what.split(",")]
    for name in chosen:
        path = targets.get(name)
        if path is None:
            warn(f"Unknown clean target '{name}' (choose from: all, {', '.join(targets)})")
            continue
        if path.exists():
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            shutil.rmtree(path)
            ok(f"Removed {path} ({fmt_bytes(size)})")
        else:
            note(f"{path} already clean")
    return 0


def write_default_profiles(dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name, desc, suffix, priority, tweaks in DEFAULT_PROFILES:
        prof = profile_from_tweaks(name, desc, suffix, tweaks, priority)
        validate_profile(prof)
        (dest_dir / f"{name}.toml").write_text(render_profile_toml(prof.sections, header=desc), encoding="utf-8")
    ok(f"Wrote {len(DEFAULT_PROFILES)} profiles to {dest_dir}")


def do_write_defaults(_: argparse.Namespace) -> int:
    banner()
    rule("Write default profiles")
    dest = PROFILES_DIR if (PROFILES_DIR.exists() and os.access(PROFILES_DIR, os.W_OK)) or not PROFILES_DIR.exists() else USER_PROFILES_DIR
    try:
        write_default_profiles(dest)
    except OSError:
        write_default_profiles(USER_PROFILES_DIR)
    return 0


def do_uninstall(args: argparse.Namespace) -> int:
    banner()
    flavor = args.uninstall.removeprefix("linux-")
    rule(f"Uninstall linux-{flavor}")
    installed = set((run(["pacman", "-Qq"], check=False).stdout or "").split())
    pkgs = [pkg for pkg in (f"linux-{flavor}-headers", f"linux-{flavor}") if pkg in installed]
    if pkgs:
        PRIV.run(["pacman", "-Rns", "--noconfirm", *pkgs], capture=False)
        ok(f"Removed packages: {', '.join(pkgs)}")
    else:
        warn(f"No installed packages named linux-{flavor}*")
    facts = host_facts()
    root = facts.xbootldr or facts.esp
    if root:
        entries = list(Path(root, "loader", "entries").glob(f"linux-{flavor}*.conf"))
        if entries:
            PRIV.run(["rm", "-f", *[str(e) for e in entries]], check=False)
            ok(f"Removed boot entries: {', '.join(e.name for e in entries)}")
    if "grub" in facts.bootloaders and have("grub-mkconfig"):
        PRIV.run(["grub-mkconfig", "-o", "/boot/grub/grub.cfg"], check=False, capture=False)
    return 0


def do_fdo_record(args: argparse.Namespace) -> int:
    banner()
    rule("AutoFDO / Propeller profile recording")
    facts = host_facts()
    p = select_profile(ensure_profiles_exist(), args.profile, facts)
    if not have("perf") or not have("create_llvm_prof"):
        raise DependencyError("perf and create_llvm_prof are required (pacman -S perf; build create_llvm_prof from google/autofdo)")
    tree = latest_tree()
    vmlinux = tree / "vmlinux" if tree else None
    if vmlinux is None or not vmlinux.is_file():
        raise BuildError("No vmlinux found; build the profile once with compiler.fdo=autofdo (profile-less first pass) before recording")
    outdir = Path(p.g("compiler", "fdo_profile_dir")).expanduser() if p.g("compiler", "fdo_profile_dir") else STATE_DIR / "fdo" / p.name
    outdir.mkdir(parents=True, exist_ok=True)
    seconds = int(args.fdo_record)
    perf_data = outdir / "perf.data"
    event = ["-e", "BR_INST_RETIRED.NEAR_TAKEN:k"] if facts.vendor == "intel" else ["--pfm-events", "RETIRED_TAKEN_BRANCH_INSTRUCTIONS:k"]
    info(f"Recording {seconds}s of system-wide kernel branch samples; run your representative workload now")
    PRIV.run(["perf", "record", *event, "-a", "-N", "-b", "-c", "500009", "-o", str(perf_data), "--", "sleep", str(seconds)], capture=False)
    PRIV.run(["chown", f"{os.getuid()}:{os.getgid()}", str(perf_data)], check=False)
    run(["create_llvm_prof", f"--binary={vmlinux}", f"--profile={perf_data}", "--format=extbinary", f"--out={outdir / 'kernel.afdo'}"], capture=False)
    ok(f"AutoFDO profile written: {outdir / 'kernel.afdo'}")
    if args.fdo_propeller:
        run(["create_llvm_prof", f"--binary={vmlinux}", f"--profile={perf_data}", "--format=propeller", "--propeller_output_module_name",
             f"--out={outdir / 'propeller_cc_profile.txt'}", f"--propeller_symorder={outdir / 'propeller_ld_profile.txt'}"], capture=False)
        ok(f"Propeller profiles written to {outdir}")
    info(f"Set compiler.fdo=autofdo{'_propeller' if args.fdo_propeller else ''} and compiler.fdo_profile_dir={outdir} in the profile, then rebuild")
    return 0


# ---------------------------------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------------------------------
DEFAULT_PROFILES: Final[tuple[tuple[str, str, str, int, dict[str, dict[str, Any]]], ...]] = (
    ("dusky_personal", "Performance Desktop: full LTO, native -march, EEVDF + CAS + scx_lavd, NTSync, lazy preemption, mitigations off", "dusky-personal", 10, {
        "meta": {"bare_metal_only": True},
        "release": {"channel": "mainline", "allow_rc": True},
        "scheduler": {"type": "eevdf", "scx": "scx_lavd", "scx_flags": "--autopilot", "scx_enable_class": True},
        "cache": {"sched_cache": True, "llc_aggr_tolerance": 1},
        "rseq": {"slice_extension": True, "slice_ext_nsec": 10000},
        "cpu": {"arch": "native", "governor": "schedutil", "amd_pstate": "active", "epp": "balance_performance", "mitigations": "off", "prefcore": True},
        "timing": {"hz": 1000, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"footprint": "standard", "thp": "madvise", "thp_defrag": "defer+madvise", "swap_backend": "zram", "zram_algo": "zstd", "zram_recomp_algo": "zstd",
                   "zram_size_pct": 50, "ksm": False, "tracing": "full"},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "full", "kcfi": False, "debug_info": "reduced", "rust": False, "headers": "auto"},
        "security": {"profile": "extreme", "acknowledge_risk": True},
        "gaming": {"ntsync": True, "uclamp": True, "split_lock_mitigate": False},
        "storage": {"io_scheduler": "none"},
        "network": {"congestion": "bbr", "qdisc": "fq", "mptcp": True},
        "modules": {"mode": "strict", "modprobed_db": True},
        "dusky": {"enhanced": True},
    }),
    ("gaming", "Gaming: BORE + scx_bpfland, 1000 Hz, full preemption, THP always, NTSync, cake, mitigations off", "dusky-gaming", 20, {
        "release": {"channel": "stable", "allow_rc": True},
        "scheduler": {"type": "bore", "scx": "scx_bpfland", "scx_flags": "-m performance", "scx_enable_class": True, "allow_vanilla_fallback": True},
        "cache": {"sched_cache": True, "llc_aggr_tolerance": 0},
        "cpu": {"arch": "native", "governor": "performance", "amd_pstate": "active", "epp": "performance", "mitigations": "off"},
        "timing": {"hz": 1000, "tickless": "idle", "preempt": "full", "preempt_dynamic": True},
        "memory": {"thp": "always", "thp_defrag": "defer+madvise", "swap_backend": "zram", "zram_algo": "zstd", "zram_recomp_algo": "zstd", "zram_size_pct": 50},
        "compiler": {"toolchain": "llvm", "lto": "thin", "debug_info": "reduced", "rust": False},
        "security": {"profile": "extreme", "acknowledge_risk": True},
        "gaming": {"ntsync": True, "uclamp": True, "max_map_count": 2147483642, "split_lock_mitigate": False, "controllers": True},
        "network": {"congestion": "bbr", "qdisc": "cake"},
        "modules": {"mode": "strict", "modprobed_db": True},
        "dusky": {"enhanced": True},
    }),
    ("low_ram", "Low RAM (<= 8 GiB): lean footprint, zram zstd, MGLRU anti-thrash, ThinLTO, strict modules", "dusky-lowram", 30, {
        "release": {"channel": "stable", "allow_rc": True},
        "scheduler": {"type": "eevdf", "scx": "none", "scx_enable_class": False},
        "cpu": {"arch": "native", "governor": "schedutil", "mitigations": "on"},
        "timing": {"hz": 500, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"footprint": "lean", "thp": "madvise", "thp_defrag": "defer", "mglru": True, "mglru_min_ttl_ms": 1000, "swap_backend": "zram", "zram_algo": "zstd",
                   "zram_recomp_algo": "zstd", "zram_size_pct": 100, "zram_multi_comp": True, "swappiness": 180, "vfs_cache_pressure": 120, "watermark_scale_factor": 125,
                   "dirty_bytes_mb": 128, "slub_tiny": False, "numa": False, "ksm": True, "damon": False, "kallsyms_all": False, "tracing": "minimal", "kexec": False,
                   "hugetlbfs": False},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "thin", "debug_info": "none", "rust": False, "headers": "auto"},
        "security": {"profile": "balanced"},
        "gaming": {"ntsync": True, "controllers": True},
        "modules": {"mode": "strict", "modprobed_db": True},
    }),
    ("minimal_strict", "Minimal strict (<= 4-8 GiB, sub-300 MiB idle target): SLUB_TINY, -Os, THP off, no BTF/tracing, DAMON reclaim, strict pruning", "dusky-minimal", 31, {
        "release": {"channel": "stable", "allow_rc": True},
        "scheduler": {"type": "eevdf", "scx": "none", "scx_enable_class": False, "autogroup": True},
        "cpu": {"arch": "native", "governor": "schedutil", "mitigations": "on"},
        "timing": {"hz": 250, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": False},
        "memory": {"footprint": "minimal", "thp": "never", "mglru": True, "mglru_min_ttl_ms": 1000, "swap_backend": "zram", "zram_algo": "zstd", "zram_recomp_algo": "zstd",
                   "zram_size_pct": 150, "zram_multi_comp": False, "swappiness": 180, "vfs_cache_pressure": 150, "watermark_scale_factor": 125, "dirty_bytes_mb": 64,
                   "slub_tiny": True, "per_vma_lock": True, "numa": False, "ksm": True, "ksm_run": False, "damon": True, "hugetlbfs": False, "kallsyms_all": False,
                   "log_buf_shift": 15, "tracing": "minimal", "kexec": False, "ikconfig": False, "trim_unused_ksyms": True},
        "compiler": {"toolchain": "llvm", "optimize": "size", "lto": "thin", "debug_info": "none", "rust": False, "headers": "never", "module_compress": "zstd"},
        "security": {"profile": "balanced", "ubsan_bounds": False},
        "gaming": {"ntsync": False, "uclamp": False, "controllers": False},
        "storage": {"io_scheduler": "mq-deadline"},
        "power": {"hibernation": False},
        "network": {"congestion": "bbr", "qdisc": "fq_codel", "mptcp": False},
        "modules": {"mode": "strict", "modprobed_db": True},
        "verify": {"require_ntsync": False, "require_btf": False},
    }),
    ("embedded_lowram", "Embedded / appliance (<= 4 GiB, headless): BASE_SMALL, SLUB_TINY, no 32-bit compat, no hibernation, NR_CPUS 8, -Os", "dusky-embedded", 32, {
        "release": {"channel": "longterm", "allow_rc": True},
        "scheduler": {"type": "eevdf", "scx": "none", "scx_enable_class": False, "autogroup": False},
        "cpu": {"arch": "generic_v3", "governor": "schedutil", "mitigations": "on", "nr_cpus": 8, "compat32": False},
        "timing": {"hz": 250, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": False},
        "memory": {"footprint": "embedded", "thp": "never", "mglru": True, "swap_backend": "zram", "zram_algo": "zstd", "zram_multi_comp": False, "zram_size_pct": 150,
                   "swappiness": 180, "vfs_cache_pressure": 200, "watermark_scale_factor": 125, "dirty_bytes_mb": 32, "slub_tiny": True, "numa": False, "ksm": False,
                   "damon": True, "hugetlbfs": False, "kallsyms_all": False, "memcg": True, "base_small": True, "log_buf_shift": 15, "tracing": "minimal", "kexec": False,
                   "ikconfig": False, "trim_unused_ksyms": True},
        "compiler": {"toolchain": "llvm", "optimize": "size", "lto": "thin", "debug_info": "none", "rust": False, "headers": "never"},
        "security": {"profile": "balanced", "ubsan_bounds": False},
        "gaming": {"ntsync": False, "uclamp": False, "controllers": False},
        "storage": {"io_scheduler": "mq-deadline"},
        "power": {"hibernation": False, "suspend": True},
        "network": {"congestion": "cubic", "qdisc": "fq_codel", "mptcp": False, "xdp": False},
        "modules": {"mode": "strict", "modprobed_db": True},
        "boot": {"nowatchdog": False},
        "verify": {"require_ntsync": False, "require_btf": False},
    }),
    ("zen4_zen5", "AMD Zen 4 / Zen 5: znver4 codegen, P-State active EPP, EEVDF + CAS + scx_lavd, ThinLTO, Rust", "dusky-zen", 40, {
        "release": {"channel": "stable", "allow_rc": True},
        "scheduler": {"type": "eevdf", "scx": "scx_lavd", "scx_flags": "--autopilot", "scx_enable_class": True},
        "cache": {"sched_cache": True, "llc_aggr_tolerance": 1},
        "cpu": {"arch": "znver4", "governor": "schedutil", "amd_pstate": "active", "epp": "balance_performance", "prefcore": True, "mitigations": "on"},
        "timing": {"hz": 1000, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"thp": "madvise", "mglru": True, "swap_backend": "zram", "zram_algo": "zstd", "zram_recomp_algo": "zstd", "zram_size_pct": 50},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "none", "kcfi": False, "debug_info": "reduced", "rust": True},
        "security": {"profile": "balanced"},
        "modules": {"mode": "strict", "modprobed_db": True},
    }),
    ("server_workstation", "Server / workstation: 250 Hz, NUMA balancing, iocost, scx_layered, full LTO, expanded drivers, headers always", "dusky-server", 50, {
        "release": {"channel": "longterm", "allow_rc": True},
        "scheduler": {"type": "eevdf", "scx": "scx_layered", "scx_enable_class": True, "sched_core": True},
        "cache": {"sched_cache": True, "llc_aggr_tolerance": 1},
        "cpu": {"arch": "generic_v3", "governor": "schedutil", "mitigations": "on"},
        "timing": {"hz": 250, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"thp": "madvise", "mglru": True, "numa": True, "numa_balancing": True, "nodes_shift": 6, "swap_backend": "zswap", "zswap_compressor": "zstd", "ksm": True},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "full", "debug_info": "reduced", "headers": "always", "rust": False},
        "security": {"profile": "balanced"},
        "gaming": {"ntsync": False, "controllers": False},
        "storage": {"io_scheduler": "mq-deadline", "iocost": True, "nvme_poll_queues": 2},
        "network": {"congestion": "bbr", "qdisc": "fq", "mptcp": True, "xdp": True},
        "modules": {"mode": "expanded", "modprobed_db": True, "keep_symbols": ["WIREGUARD", "TUN", "VETH", "BRIDGE", "VLAN_8021Q", "MACVLAN", "NF_TABLES", "OVERLAY_FS"]},
        "verify": {"require_ntsync": False},
    }),
    ("battery_efficiency", "Battery: powersave + EPP power, teo, RCU lazy, power-efficient workqueues, ASPM powersupersave, 300 Hz", "dusky-battery", 60, {
        "release": {"channel": "stable", "allow_rc": True},
        "scheduler": {"type": "eevdf", "scx": "scx_lavd", "scx_flags": "--autopower", "scx_enable_class": True},
        "cache": {"sched_cache": True},
        "cpu": {"arch": "native", "governor": "powersave", "epp": "balance_power", "amd_pstate": "active", "mitigations": "on"},
        "timing": {"hz": 300, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"footprint": "lean", "thp": "madvise", "swap_backend": "zram", "zram_algo": "zstd", "zram_recomp_algo": "zstd", "zram_size_pct": 50, "tracing": "minimal"},
        "power": {"wq_power_efficient": True, "cpu_idle_governor": "teo", "rcu_lazy": True, "energy_model": True, "pcie_aspm": "powersupersave", "hda_power_save": 10},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "thin", "debug_info": "none", "rust": False},
        "security": {"profile": "balanced"},
        "storage": {"io_scheduler": "none"},
        "modules": {"mode": "strict", "modprobed_db": True},
    }),
    ("vm_guest", "VM guest (KVM/QEMU, Hyper-V, VMware): paravirt, virtio, free page reporting, haltpoll, memory hotplug, portable v3 codegen", "dusky-vm", 70, {
        "meta": {"portable_package": True},
        "release": {"channel": "stable", "allow_rc": True},
        "scheduler": {"type": "eevdf", "scx": "none", "scx_enable_class": True},
        "cache": {"sched_cache": False},
        "cpu": {"arch": "generic_v3", "governor": "schedutil", "amd_pstate": "undefined", "mitigations": "on", "nr_cpus": 64},
        "timing": {"hz": 250, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"footprint": "lean", "thp": "madvise", "swap_backend": "zram", "zram_algo": "zstd", "zram_size_pct": 50, "page_reporting": True, "numa": False, "tracing": "minimal"},
        "power": {"cpu_idle_governor": "haltpoll", "hibernation": False},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "thin", "debug_info": "none", "rust": False, "headers": "auto"},
        "security": {"profile": "balanced"},
        "gaming": {"ntsync": False, "controllers": False},
        "storage": {"io_scheduler": "none"},
        "modules": {"mode": "expanded", "modprobed_db": True, "allow_lsmod_fallback": True},
        "verify": {"require_ntsync": False},
    }),
    ("hardened", "Hardened: KSPP-style hardening, kCFI + FineIBT, init_on_free, strict IOMMU, lockdown early, mitigations on, EEVDF + CAS", "dusky-hardened", 80, {
        "release": {"channel": "stable", "allow_rc": True},
        "scheduler": {"type": "eevdf", "scx": "none", "scx_enable_class": True, "sched_core": True},
        "cache": {"sched_cache": True},
        "cpu": {"arch": "native", "governor": "schedutil", "mitigations": "on"},
        "timing": {"hz": 1000, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"thp": "madvise", "mglru": True, "swap_backend": "zram", "zram_algo": "zstd", "slab_buckets": True, "ksm": False},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "thin", "kcfi": True, "debug_info": "reduced", "rust": False},
        "security": {"profile": "hardened", "apparmor": True, "lockdown_early": True},
        "gaming": {"ntsync": True, "split_lock_mitigate": True},
        "network": {"congestion": "bbr", "qdisc": "fq_codel"},
        "modules": {"mode": "strict", "modprobed_db": True, "sig_force": False},
        "boot": {"nowatchdog": False},
    }),
)


# ---------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------
EPILOG: Final = textwrap.dedent(f"""\
    examples:
      %(prog)s --write-default-profiles        write the built-in profiles
      %(prog)s --doctor                        host telemetry, toolchain and bootloader diagnostics
      %(prog)s -p low_ram                      build; answers 'use defaults exactly?' [Y/n] first
      %(prog)s -p gaming --wizard --no-install walk every knob, build packages only
      %(prog)s -p zen4_zen5 --configure-only --print-matrix
      %(prog)s --export-bundle / --import-bundle FILE   cross-machine hardware bundles
      %(prog)s --uninstall dusky-gaming        remove packages and boot entries
    environment: DUSKY_PROFILES_DIR DUSKY_BUILD_DIR DUSKY_PATCH_CACHE DUSKY_THINLTO_CACHE DUSKY_PKGDEST DUSKY_CPU_ARCH DUSKY_LTO DUSKY_JOBS ...
    exit codes: 1 generic, 2 profile, 3 network, 4 verification, 5 build, 6 dependency, 130 aborted
    """)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="dusky_kernal_compile.py", description=f"{APP_NAME} v{APP_VERSION} -- {APP_TAGLINE}", epilog=EPILOG,
                                 formatter_class=argparse.RawDescriptionHelpFormatter, suggest_on_error=True, color=True)
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    ap.add_argument("-p", "--profile", metavar="NAME", help="profile to build (interactive picker when omitted)")
    mode = ap.add_argument_group("modes")
    mode.add_argument("-l", "--list-profiles", action="store_true", help="list profiles")
    mode.add_argument("--show", action="store_true", help="show the resolved profile")
    mode.add_argument("--dump-toml", action="store_true", help="with --show: print the resolved profile as TOML")
    mode.add_argument("--spec", action="store_true", help="print the profile schema")
    mode.add_argument("--print-matrix", action="store_true", help="print the Kconfig matrix (dry-run without a build, or after configuration during a build)")
    mode.add_argument("--doctor", action="store_true", help="system diagnostics")
    mode.add_argument("--clean", metavar="WHAT", nargs="?", const="all", help="clean all|src|tarballs|patches|packages|thinlto|logs|seeds (comma separated)")
    mode.add_argument("--write-default-profiles", action="store_true", help="write the built-in profiles")
    mode.add_argument("--export-bundle", nargs="?", const="", default=None, metavar="FILE", help="export a hardware bundle for remote builds")
    mode.add_argument("--import-bundle", type=Path, metavar="FILE", help="import a hardware bundle and register remote_<host>")
    mode.add_argument("--uninstall", metavar="FLAVOR", help="remove linux-<flavor>{,-headers} and boot entries")
    mode.add_argument("--fdo-record", metavar="SECONDS", help="record an AutoFDO profile for --profile (needs perf + create_llvm_prof)")
    mode.add_argument("--fdo-propeller", action="store_true", help="with --fdo-record: also emit Propeller profiles")
    mode.add_argument("--menu", action="store_true", help="interactive main menu")
    ov = ap.add_argument_group("overrides (applied before the wizard question)")
    ov.add_argument("--cpu-arch", choices=list(CPU_ARCHES), metavar="ARCH")
    ov.add_argument("--modules-mode", choices=list(MODULES_MODE_CHOICES))
    ov.add_argument("--toolchain", choices=list(TOOLCHAIN_CHOICES))
    ov.add_argument("--lto", choices=list(LTO_CHOICES))
    ov.add_argument("--channel", choices=list(CHANNEL_CHOICES))
    ov.add_argument("--scheduler", choices=list(SCHED_CHOICES))
    ov.add_argument("--scx", choices=list(SCX_CHOICES))
    ov.add_argument("--headers", choices=list(HEADERS_CHOICES))
    ov.add_argument("--no-headers", action="store_const", dest="headers", const="never")
    ov.add_argument("--footprint", choices=list(FOOTPRINT_CHOICES))
    ov.add_argument("--pin", metavar="VERSION", help="exact kernel version (7.2.3, 7.3-rc2)")
    ov.add_argument("-j", "--jobs", type=int)
    ov.add_argument("--no-rust", action="store_true")
    bh = ap.add_argument_group("build behaviour")
    bh.add_argument("--build-dir", type=Path, metavar="DIR", help="build directory (default: /mnt/zram1/dusky_kernel or ~/.cache/dusky-kernel)")
    bh.add_argument("--wizard", action="store_true", help="always enter the granular configuration wizard")
    bh.add_argument("--no-prompt", action="store_true", help="never ask the wizard question (use profile defaults)")
    bh.add_argument("--fresh", action="store_true", help="re-extract the source tree")
    bh.add_argument("--seed-config", metavar="FILE", help="seed .config from FILE")
    bh.add_argument("--configure-only", action="store_true", help="stop after configuration + verification")
    bh.add_argument("--no-install", action="store_true", help="build packages but do not install")
    bh.add_argument("--kernel-install", action="store_true", help="also register via kernel-install(8)")
    bh.add_argument("--force", action="store_true", help="override bare_metal_only guards")
    bh.add_argument("-y", "--yes", action="store_true", help="assume defaults for every question")
    bh.add_argument("-v", "--verbose", action="store_true")
    bh.add_argument("--json", action="store_true", help="machine-readable output for --doctor/--show")
    bh.add_argument("--no-color", action="store_true")
    return ap


# ---------------------------------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------------------------------
def install_aur_package(pkg: str) -> bool:
    """Install an AUR package using paru, yay, or direct makepkg without sudo."""
    if have("paru"):
        return run(["paru", "-S", "--needed", pkg], capture=False).returncode == 0
    if have("yay"):
        return run(["yay", "-S", "--needed", pkg], capture=False).returncode == 0
    info(f"No AUR helper detected; building {pkg} directly from AUR via makepkg...")
    with tempfile.TemporaryDirectory(prefix=f"aur-{pkg}-") as tmp:
        clone = run(["git", "clone", f"https://aur.archlinux.org/{pkg}.git", str(tmp)], capture=False)
        if clone.returncode != 0:
            return False
        return run(["makepkg", "-si", "--noconfirm", "--needed"], cwd=Path(tmp), capture=False).returncode == 0


def initialize_toolchains() -> None:
    rule("Toolchains & hardware profiler")
    official_pkgs = ["base-devel", "clang", "lld", "llvm", "rust", "rust-bindgen", "bc", "cpio", "kmod", "pahole", "perf", "curl", "gnupg", "terminus-font"]
    if ask_yes(f"Install official packages (pacman -S --needed {' '.join(official_pkgs)}) ?", True):
        PRIV.run(["pacman", "-S", "--needed", *official_pkgs], capture=False)

    if not have("modprobed-db"):
        if ask_yes("modprobed-db is an AUR package (tracks loaded modules for localmodconfig); install from AUR now?", True):
            if install_aur_package("modprobed-db"):
                ok("modprobed-db installed successfully from AUR")
            else:
                warn("Could not install modprobed-db automatically; install manually with: paru -S modprobed-db")

    if have("modprobed-db"):
        run(["modprobed-db", "store"], check=False)
        ok("modprobed-db storing loaded modules (keep using the machine before strict builds)")


def live_telemetry() -> None:
    rule("Live hardware telemetry")
    facts = host_facts()
    load = _read("/proc/loadavg").split()[:3]
    mem = _read("/proc/meminfo")

    def kib(key: str) -> int:
        m = re.search(rf"^{key}:\s+(\d+)", mem, re.M)
        return int(m.group(1)) if m else 0

    used = kib("MemTotal") - kib("MemAvailable")
    rows = [["load", " ".join(load)], ["memory used", f"{used / 1048576:.2f} GiB of {facts.mem_gib:.1f} GiB (available {kib('MemAvailable') / 1048576:.2f} GiB)"],
            ["kernel slab", f"{kib('Slab') / 1024:.0f} MiB (SReclaimable {kib('SReclaimable') / 1024:.0f} MiB)"], ["page tables", f"{kib('PageTables') / 1024:.0f} MiB"],
            ["swap", f"{(kib('SwapTotal') - kib('SwapFree')) / 1048576:.2f} GiB used of {kib('SwapTotal') / 1048576:.2f} GiB"],
            ["LLC topology", f"{facts.llc_domains} L3 domain(s) x {facts.llc_kib // 1024} MiB"], ["sched_ext", _read("/sys/kernel/sched_ext/root/ops").strip() or ("enabled, no scheduler loaded" if facts.sched_ext_live else "not available")],
            ["cpufreq", f"{_read('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor').strip() or 'n/a'} / EPP {_read('/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference').strip() or 'n/a'}"],
            ["cpuidle", _read("/sys/devices/system/cpu/cpuidle/current_governor").strip() or "n/a"], ["THP", _read("/sys/kernel/mm/transparent_hugepage/enabled").strip() or "n/a"],
            ["MGLRU", _read("/sys/kernel/mm/lru_gen/enabled").strip() or "n/a"], ["preempt", _read("/sys/kernel/debug/sched/preempt").strip() or "n/a (debugfs)"]]
    for z in Path("/sys/block").glob("zram*"):
        rows.append([z.name, f"{_read(z / 'comp_algorithm').strip()} disksize {int(_read(z / 'disksize').strip() or 0) >> 20} MiB"])
    table(["metric", "value"], rows)


def config_manager_menu() -> None:
    while True:
        rule("Configuration manager")
        say(" 1) List profiles\n 2) Show a profile\n 3) Run the wizard on a profile and save as new profile\n 4) Write built-in default profiles\n 5) Print schema\n 6) Back\n")
        choice = ask_index("Select", 6, 6)
        facts = host_facts()
        match choice:
            case 1:
                print_profile_table(ensure_profiles_exist(), facts)
            case 2:
                p = select_profile(ensure_profiles_exist(), None, facts).clone()
                show_configuration(p, normalize_profile(p))
            case 3:
                p = select_profile(ensure_profiles_exist(), None, facts).clone()
                diff = run_wizard(p, facts)
                diff = wizard_review_loop(p, facts, diff, force=False)
                show_configuration(p, diff)
                offer_save_profile(p)
            case 4:
                do_write_defaults(argparse.Namespace())
            case 5:
                do_spec(argparse.Namespace())
            case _:
                return
        pause()


def bundle_manager_menu() -> None:
    rule("Bundle manager")
    say(" 1) Export this machine's hardware bundle\n 2) Import a bundle\n 3) Back\n")
    match ask_index("Select", 3, 3):
        case 1:
            do_export_bundle(None)
        case 2:
            do_import_bundle(Path(ask("Bundle path", "")).expanduser())
        case _:
            return


def interactive_menu() -> int:
    while True:
        say("")
        banner()
        say(f"{C.ACCENT}  Main menu{C.RESET}")
        say(" 1) Install toolchains & snapshot the hardware profiler (modprobed-db)\n 2) Live hardware telemetry\n 3) Diagnostics (--doctor)\n 4) Configuration manager & profiles\n"
            " 5) Export / import remote hardware bundle\n 6) Compile & install a kernel (profile picker)\n 7) Uninstall a Dusky flavor\n 8) Clean caches\n 9) Exit\n")
        try:
            choice = ask_index("Select", 9, 6)
        except KeyboardInterrupt:
            return 0
        try:
            match choice:
                case 1:
                    initialize_toolchains()
                case 2:
                    live_telemetry()
                case 3:
                    do_doctor(argparse.Namespace(json=False))
                case 4:
                    config_manager_menu()
                    continue
                case 5:
                    bundle_manager_menu()
                case 6:
                    args = build_parser().parse_args([])
                    do_build(args)
                case 7:
                    flavor = ask("Flavor suffix to uninstall (e.g. dusky-gaming)", "")
                    if flavor:
                        do_uninstall(argparse.Namespace(uninstall=flavor))
                case 8:
                    do_clean(argparse.Namespace(clean=ask("What to clean (all|src|tarballs|patches|packages|thinlto|logs|seeds)", "packages,logs")))
                case _:
                    return 0
        except KeyboardInterrupt:
            _ABORT.clear()
            warn("Action cancelled")
        except DuskyError as e:
            _ABORT.clear()
            err(str(e))
        pause()


def main(argv: Sequence[str] | None = None) -> int:
    global _VERBOSE, ASSUME_YES
    args = build_parser().parse_args(argv)
    if getattr(args, "build_dir", None):
        set_build_dir(args.build_dir)
    _VERBOSE, ASSUME_YES = bool(args.verbose), bool(args.yes)
    if args.no_color or not sys.stdout.isatty() or os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        C.disable()
    install_signal_handlers()
    try:
        if args.export_bundle is not None:
            do_export_bundle(Path(args.export_bundle).expanduser() if args.export_bundle else None)
            return 0
        if args.import_bundle:
            do_import_bundle(args.import_bundle.expanduser())
            return 0
        if args.uninstall:
            return do_uninstall(args)
        if args.fdo_record:
            return do_fdo_record(args)
        if args.spec:
            return do_spec(args)
        if args.write_default_profiles:
            return do_write_defaults(args)
        if args.doctor:
            return do_doctor(args)
        if args.clean is not None:
            return do_clean(args)
        if args.list_profiles:
            return do_list(args)
        if args.show:
            return do_show(args)
        if args.print_matrix and not args.configure_only:
            return do_matrix(args)
        wants_build = bool(args.profile or args.configure_only or args.no_install or args.wizard or args.yes)
        if args.menu or (not wants_build and interactive()):
            return interactive_menu()
        return do_build(args)
    except DuskyError as e:
        err(str(e))
        if not isinstance(e, AbortError):
            send_notification("Kernel build failed", str(e)[:200], urgency="critical", icon="dialog-error")
        return e.exit_code
    except KeyboardInterrupt:
        _reap_all()
        sys.stdout.write(C.SHOW + "\n")
        warn("Interrupted -- child process groups terminated")
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    finally:
        _reap_all()
        PRIV.stop()
        JOURNAL.close()


if __name__ == "__main__":
    raise SystemExit(main())
