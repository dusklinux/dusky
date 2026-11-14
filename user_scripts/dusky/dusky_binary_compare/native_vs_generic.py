#!/usr/bin/env python3
"""
native_vs_generic.py - Comprehensive Microarchitectural & Performance Benchmark Tool.
Profiles and compares compiled binaries (Rust, C/C++, Go, etc.) for CPU cycles,
IPC, ISA specialization (AVX-512, AVX2, AVX-VNNI, FMA3, BMI2, VAES/SHA-NI),
execution latency, scheduler context switches, and memory footprint.

Supports:
- Single Binary Profiling (records metrics & auto-diffs against historical runs)
- Head-to-Head Comparison (Native vs. Generic with interleaved ABBA execution)
- Fast-Path CLI & Full Wayland GUI Lifecycle testing (direct Hyprland IPC socket)

Target: Arch Linux | Kernel 7.3+ | Python 3.15+ | Hyprland (Wayland)
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
import contextlib
import dataclasses
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

console = (
    Console(width=max(128, shutil.get_terminal_size((128, 24)).columns))
    if RICH_AVAILABLE
    else None
)

HISTORY_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "dusky"
    / "settings"
    / "binary_compare"
)
HISTORY_FILE = HISTORY_DIR / "history.json"
MAX_HISTORY_ENTRIES = 100

# Active child process group IDs (PGIDs) for signal-safe teardown
_ACTIVE_PGIDS: set[int] = set()

# Pre-allocated O(1) ISA classification tables for zero-regex streaming disassembly
_NON_SIMD_V_OPS: frozenset[str] = frozenset({
    "verr", "verw", "vmcall", "vmlaunch", "vmresume", "vmxoff", "vmxon",
    "vmread", "vmwrite", "vmclear", "vmptrld", "vmptrst", "vmmcall",
    "vmload", "vmsave", "vmrun", "vzeroall", "vzeroupper",
})

_EVEX_MNEMONIC_PREFIXES: tuple[str, ...] = (
    "vpternlog", "vpcompress", "vpexpand", "vpermt2", "vpermi2", "vpmovm2",
    "vpmovb2m", "vpmovw2m", "vpmovd2m", "vpmovq2m", "vfpclass", "vrcp14",
    "vrsqrt14", "vscalef", "vfixupimm", "valignd", "valignq", "vdbpsadbw",
    "vplzcnt", "vpopcnt", "vpshld", "vpshrd", "vprorv", "vprolv",
)

_AVX_VNNI_OPS: frozenset[str] = frozenset({
    "vpdpbusd", "vpdpbusds", "vpdpwssd", "vpdpwssds", "vpdpbssd", "vpdpbssds",
})

_FMA3_PREFIXES: tuple[str, ...] = (
    "vfmadd", "vfmsub", "vfnmadd", "vfnmsub", "vfmaddsub", "vfmsubadd",
)

_VEC_CRYPTO_OPS: frozenset[str] = frozenset({
    "vaesenc", "vaesenclast", "vaesdec", "vaesdeclast", "vpclmulqdq",
    "vgf2p8affineqb", "vgf2p8affineinvqb", "vgf2p8mulb",
    "vsha512rnds2", "vsha512msg1", "vsha512msg2", "vsm3msg1", "vsm3msg2",
    "vsm3rnds2", "vsm4key4", "vsm4rnds4",
})

_VEC_NT_OPS: frozenset[str] = frozenset({
    "vmovntdq", "vmovntdqa", "vmovntps", "vmovntpd",
})

_BMI1_BMI2_OPS: frozenset[str] = frozenset({
    "andn", "bextr", "blsi", "blsmsk", "blsr",
    "bzhi", "mulx", "pdep", "pext", "rorx", "sarx", "shlx", "shrx",
})

_BITMANIP_SCALAR_OPS: frozenset[str] = frozenset({
    "lzcnt", "tzcnt", "popcnt", "movbe", "adcx", "adox",
    "lzcntw", "lzcntl", "lzcntq", "tzcntw", "tzcntl", "tzcntq",
    "popcntw", "popcntl", "popcntq", "movbew", "movbel", "movbeq",
})

_SCALAR_CRYPTO_OPS: frozenset[str] = frozenset({
    "aesenc", "aesenclast", "aesdec", "aesdeclast", "aesimc", "aeskeygenassist",
    "pclmulqdq", "sha1rnds4", "sha1nexte", "sha1msg1", "sha1msg2",
    "sha256rnds2", "sha256msg1", "sha256msg2",
    "gf2p8affineqb", "gf2p8affineinvqb", "gf2p8mulb",
    "crc32", "crc32b", "crc32w", "crc32l", "crc32q",
})

_SCALAR_CACHE_NT_OPS: frozenset[str] = frozenset({
    "movntdq", "movntdqa", "movnti", "movntil", "movntiq", "movntps", "movntpd",
    "prefetcht0", "prefetcht1", "prefetcht2", "prefetchnta", "prefetchw",
    "prefetchwt1", "clflush", "clflushopt", "clwb",
})


# ---------------------------------------------------------------------------
# Subprocess Environment & Process Group Lifecycle Management
# ---------------------------------------------------------------------------


def _clean_c_env() -> dict[str, str]:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


def cleanup_active_pgids() -> None:
    if not _ACTIVE_PGIDS:
        return
    snapshot = list(_ACTIVE_PGIDS)
    for pgid in snapshot:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    time.sleep(0.03)
    for pgid in snapshot:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            os.waitpid(-pgid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass
        _ACTIVE_PGIDS.discard(pgid)


# ---------------------------------------------------------------------------
# Telemetry Data Structures & Statistical Engine
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class SampleMetrics:
    wall_ms: float = 0.0
    user_cpu_ms: float = 0.0
    sys_cpu_ms: float = 0.0
    max_rss_kib: int = 0
    pss_kib: int = 0
    min_flt: int = 0
    maj_flt: int = 0
    vol_ctxt: int = 0
    invol_ctxt: int = 0
    pcore_cycles: int = 0
    pcore_instructions: int = 0
    ecore_cycles: int = 0
    ecore_instructions: int = 0
    cache_refs: int = 0
    task_clock_ms: float = 0.0
    gui_ready_ms: float = 0.0
    gui_teardown_ms: float = 0.0
    gui_mapped: bool = False


_SAMPLE_FIELD_NAMES: frozenset[str] = frozenset(
    f.name for f in dataclasses.fields(SampleMetrics)
)


def sample_ipc(s: SampleMetrics) -> float:
    if s.pcore_cycles > 0:
        return s.pcore_instructions / s.pcore_cycles
    total_cyc = s.pcore_cycles + s.ecore_cycles
    if total_cyc > 0:
        return (s.pcore_instructions + s.ecore_instructions) / total_cyc
    return 0.0


@dataclasses.dataclass(slots=True)
class StatSummary:
    mean: float
    median: float
    stdev: float
    cv_pct: float
    min_val: float
    max_val: float
    p95: float
    iqr: float


def compute_stats(values: list[float]) -> StatSummary:
    if not values:
        return StatSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    s = sorted(values)
    n = len(s)
    mean = statistics.fmean(s)
    median = statistics.median(s)
    stdev = statistics.stdev(s) if n > 1 else 0.0
    cv_pct = (stdev / abs(mean) * 100.0) if (n > 1 and mean != 0.0) else 0.0
    p95_idx = min(n - 1, max(0, math.ceil(0.95 * n) - 1))
    if n >= 2:
        q1, _, q3 = statistics.quantiles(s, n=4)
        iqr = q3 - q1
    else:
        iqr = 0.0
    return StatSummary(
        mean=mean,
        median=median,
        stdev=stdev,
        cv_pct=cv_pct,
        min_val=s[0],
        max_val=s[-1],
        p95=s[p95_idx],
        iqr=iqr,
    )


def mann_whitney_u_pvalue(x: list[float], y: list[float]) -> float:
    n1, n2 = len(x), len(y)
    if n1 < 5 or n2 < 5:
        return 1.0
    combined = sorted([(v, 0) for v in x] + [(v, 1) for v in y], key=lambda item: item[0])
    ranks = [0.0] * len(combined)
    tie_sum = 0.0
    i = 0
    n = n1 + n2
    while i < n:
        j = i
        while j < n and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[k] = avg_rank
        t = j - i
        if t > 1:
            tie_sum += t**3 - t
        i = j

    r1 = sum(ranks[idx] for idx, (_, grp) in enumerate(combined) if grp == 0)
    u1 = r1 - (n1 * (n1 + 1)) / 2.0
    u = min(u1, n1 * n2 - u1)
    mu = (n1 * n2) / 2.0
    tie_corr = tie_sum / (n * (n - 1)) if n > 1 else 0.0
    sigma_sq = (n1 * n2 / 12.0) * ((n + 1) - tie_corr)
    if sigma_sq < 1e-12:
        return 1.0
    z = max(0.0, abs(u - mu) - 0.5) / math.sqrt(sigma_sq)
    return max(0.0, min(1.0, math.erfc(z / math.sqrt(2.0))))


# ---------------------------------------------------------------------------
# Hardware Topology & CPU Management
# ---------------------------------------------------------------------------


def parse_cpulist(text: str) -> set[int]:
    cpus: set[int] = set()
    for part in text.strip().split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            cpus.update(range(int(a), int(b) + 1))
        else:
            cpus.add(int(part))
    return cpus


def detect_hybrid_cpus() -> tuple[set[int], set[int]]:
    p_path = Path("/sys/devices/cpu_core/cpus")
    e_path = Path("/sys/devices/cpu_atom/cpus")
    p_cores = parse_cpulist(p_path.read_text(encoding="utf-8")) if p_path.is_file() else set()
    e_cores = parse_cpulist(e_path.read_text(encoding="utf-8")) if e_path.is_file() else set()
    return p_cores, e_cores


@contextlib.contextmanager
def scoped_cpu_affinity(cpus: set[int] | None) -> Iterator[None]:
    orig = os.sched_getaffinity(0)
    try:
        if cpus:
            os.sched_setaffinity(0, cpus)
        yield
    finally:
        try:
            os.sched_setaffinity(0, orig)
        except OSError:
            pass


def get_cpu_model() -> str:
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
        if m := re.search(r"model name\s+:\s+(.+)", cpuinfo):
            return m.group(1).strip()
    except (OSError, ValueError):
        pass
    return "Unknown Processor"


def get_scaling_governor() -> str:
    try:
        gov = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").read_text(encoding="utf-8").strip()
        epp_path = Path("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")
        if epp_path.is_file():
            epp = epp_path.read_text(encoding="utf-8").strip()
            return f"{gov} ({epp})"
        return gov
    except (OSError, ValueError):
        pass
    return "Default"


# ---------------------------------------------------------------------------
# Static ISA & Disassembly Audit (Streaming, Zero-Regex, Single-Split)
# ---------------------------------------------------------------------------


def sha256_short(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def analyze_binary_isa(binary: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "size_bytes": binary.stat().st_size,
        "sha256": sha256_short(binary),
        "total_insns": 0,
        "avx512_evex_insns": 0,
        "avx_vex_insns": 0,
        "avx_vnni_insns": 0,
        "fma3_insns": 0,
        "bmi1_bmi2_insns": 0,
        "bitmanip_cnt_insns": 0,
        "crypto_hash_insns": 0,
        "cache_nt_insns": 0,
    }
    objdump = shutil.which("objdump")
    if not objdump:
        return info

    proc = subprocess.Popen(
        [objdump, "-d", "--no-show-raw-insn", str(binary)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_clean_c_env(),
    )

    total_insns = 0
    avx512_evex = 0
    avx_vex = 0
    avx_vnni = 0
    fma3 = 0
    bmi1_bmi2 = 0
    bitmanip_cnt = 0
    crypto_hash = 0
    cache_nt = 0

    try:
        if proc.stdout:
            for line in proc.stdout:
                if not line.startswith(" "):
                    continue
                colon_idx = line.find(":")
                if colon_idx == -1:
                    continue
                rest = line[colon_idx + 1 :].lstrip()
                if not rest:
                    continue
                parts = rest.split(None, 1)
                m = parts[0].lower()
                if m in ("lock", "rep", "repz", "repnz", "repe", "repne", "data16", "rex64", "bnd"):
                    if len(parts) < 2:
                        continue
                    parts = parts[1].split(None, 1)
                    m = parts[0].lower()
                operands = parts[1].lower() if len(parts) > 1 else ""

                total_insns += 1

                if m[0] == "v":
                    if m in _NON_SIMD_V_OPS:
                        continue
                    if (
                        "zmm" in operands
                        or "{k" in operands
                        or "{z}" in operands
                        or "{1to" in operands
                        or m.startswith(_EVEX_MNEMONIC_PREFIXES)
                    ):
                        avx512_evex += 1
                    elif m in _AVX_VNNI_OPS:
                        avx_vnni += 1
                    elif m.startswith(_FMA3_PREFIXES):
                        fma3 += 1
                    elif m in _VEC_CRYPTO_OPS:
                        crypto_hash += 1
                    elif m in _VEC_NT_OPS:
                        cache_nt += 1
                    else:
                        avx_vex += 1
                else:
                    if m in _BMI1_BMI2_OPS:
                        bmi1_bmi2 += 1
                    elif m in _BITMANIP_SCALAR_OPS:
                        bitmanip_cnt += 1
                    elif m in _SCALAR_CRYPTO_OPS:
                        crypto_hash += 1
                    elif m in _SCALAR_CACHE_NT_OPS:
                        cache_nt += 1
    finally:
        proc.wait()

    info["total_insns"] = total_insns
    info["avx512_evex_insns"] = avx512_evex
    info["avx_vex_insns"] = avx_vex
    info["avx_vnni_insns"] = avx_vnni
    info["fma3_insns"] = fma3
    info["bmi1_bmi2_insns"] = bmi1_bmi2
    info["bitmanip_cnt_insns"] = bitmanip_cnt
    info["crypto_hash_insns"] = crypto_hash
    info["cache_nt_insns"] = cache_nt
    return info


# ---------------------------------------------------------------------------
# Hardware PMU Performance Counters (perf stat)
# ---------------------------------------------------------------------------


def detect_perf_events(allow_hybrid: bool = False) -> list[str]:
    perf = shutil.which("perf")
    if not perf:
        return []
    env = _clean_c_env()
    has_pcore = Path("/sys/devices/cpu_core").exists()
    has_ecore = Path("/sys/devices/cpu_atom").exists()

    candidates: list[list[str]] = []
    if has_pcore:
        base_hybrid = ["task-clock", "cpu_core/cycles/", "cpu_core/instructions/", "cache-references"]
        if has_ecore and allow_hybrid:
            base_hybrid.extend(["cpu_atom/cycles/", "cpu_atom/instructions/"])
        candidates.append(base_hybrid)

        no_cache_hybrid = ["task-clock", "cpu_core/cycles/", "cpu_core/instructions/"]
        if has_ecore and allow_hybrid:
            no_cache_hybrid.extend(["cpu_atom/cycles/", "cpu_atom/instructions/"])
        candidates.append(no_cache_hybrid)

    candidates.append(["task-clock", "cycles", "instructions", "cache-references"])
    candidates.append(["task-clock", "cycles", "instructions"])

    for events in candidates:
        res = subprocess.run(
            [perf, "stat", "-x", ";", "-e", ",".join(events), "--", "true"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if res.returncode == 0 and "<not supported>" not in res.stderr:
            return events
    return []


def parse_perf_csv(stderr_text: str, sample: SampleMetrics) -> None:
    for line in stderr_text.splitlines():
        parts = line.split(";")
        if len(parts) < 3:
            continue
        raw_val, event_raw = parts[0].strip(), parts[2].strip()
        if not raw_val or raw_val.startswith("<"):
            continue
        event_name = event_raw.split(":")[0].strip("/")
        for suffix in ("/u", "/k", "/uk", "/ku"):
            if event_name.endswith(suffix):
                event_name = event_name[: -len(suffix)].strip("/")
                break
        try:
            val = float(raw_val.replace(",", ""))
        except ValueError:
            continue

        if event_name == "task-clock":
            sample.task_clock_ms = val
        elif event_name in ("cpu_core/cycles", "cycles", "cpu-cycles"):
            sample.pcore_cycles = int(val)
        elif event_name in ("cpu_core/instructions", "instructions"):
            sample.pcore_instructions = int(val)
        elif event_name == "cpu_atom/cycles":
            sample.ecore_cycles = int(val)
        elif event_name == "cpu_atom/instructions":
            sample.ecore_instructions = int(val)
        elif event_name in ("cache-references", "cpu_core/cache-references"):
            sample.cache_refs = int(val)


# ---------------------------------------------------------------------------
# Process Execution, Direct Hyprland Socket Polling & Telemetry Capture
# ---------------------------------------------------------------------------


def read_proc_memory_kib(pid: int) -> tuple[int, int]:
    vm_hwm, pss = 0, 0
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                vm_hwm = int(line.split()[1])
                break
    except (OSError, ValueError, IndexError):
        pass
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text(encoding="utf-8").splitlines():
            if line.startswith("Pss:"):
                pss = int(line.split()[1])
                break
    except (OSError, ValueError, IndexError):
        pass
    return vm_hwm, pss


def reap_popen(proc: subprocess.Popen[Any], timeout_s: float = 2.0) -> Any:
    pid = proc.pid
    try:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                pid_done, status, rusage = os.wait4(pid, os.WNOHANG)
                if pid_done == pid:
                    proc.returncode = os.waitstatus_to_exitcode(status)
                    return rusage
            except ChildProcessError:
                return None
            time.sleep(0.0005)

        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

        grace_deadline = time.monotonic() + 0.15
        while time.monotonic() < grace_deadline:
            try:
                pid_done, status, rusage = os.wait4(pid, os.WNOHANG)
                if pid_done == pid:
                    proc.returncode = os.waitstatus_to_exitcode(status)
                    return rusage
            except ChildProcessError:
                return None
            time.sleep(0.002)

        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

        try:
            _, status, rusage = os.wait4(pid, 0)
            proc.returncode = os.waitstatus_to_exitcode(status)
            return rusage
        except ChildProcessError:
            return None
    finally:
        _ACTIVE_PGIDS.discard(pid)


def _get_hyprland_socket_path() -> Path | None:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        return None
    xdg_run = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_run:
        candidate = Path(xdg_run) / "hypr" / sig / ".socket.sock"
        if candidate.is_socket():
            return candidate
    fallback = Path("/tmp/hypr") / sig / ".socket.sock"
    if fallback.is_socket():
        return fallback
    return None


def _query_hyprland_socket(sock_path: Path, command: bytes) -> str | None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.05)
            s.connect(str(sock_path))
            s.sendall(command)
            chunks: list[bytes] = []
            while True:
                data = s.recv(65536)
                if not data:
                    break
                chunks.append(data)
            return b"".join(chunks).decode("utf-8", errors="replace")
    except OSError:
        return None


def _pid_matches_pgid(candidate_pid: Any, target_pid: int) -> bool:
    if not isinstance(candidate_pid, int) or candidate_pid <= 0:
        return False
    if candidate_pid == target_pid:
        return True
    try:
        return os.getpgid(candidate_pid) == target_pid
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _json_contains_matching_pid(node: Any, target_pid: int) -> bool:
    if isinstance(node, dict):
        if "pid" in node and _pid_matches_pgid(node["pid"], target_pid):
            return True
        return any(_json_contains_matching_pid(v, target_pid) for v in node.values())
    if isinstance(node, list):
        return any(_json_contains_matching_pid(item, target_pid) for item in node)
    return False


def check_hyprland_surface_mapped(pid: int) -> bool:
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return False

    sock_path = _get_hyprland_socket_path()
    if sock_path is not None:
        for cmd in (b"j/clients", b"j/layers"):
            raw = _query_hyprland_socket(sock_path, cmd)
            if raw:
                try:
                    parsed = json.loads(raw)
                    if _json_contains_matching_pid(parsed, pid):
                        return True
                except json.JSONDecodeError:
                    if f'"pid": {pid}' in raw or f'"pid":{pid}' in raw:
                        return True
        return False

    hyprctl = shutil.which("hyprctl")
    if not hyprctl:
        return False
    for subcmd in ("clients", "layers"):
        try:
            res = subprocess.run(
                [hyprctl, subcmd, "-j"],
                capture_output=True,
                text=True,
                timeout=0.12,
                check=False,
            )
            if res.returncode == 0 and res.stdout:
                try:
                    parsed = json.loads(res.stdout)
                    if _json_contains_matching_pid(parsed, pid):
                        return True
                except json.JSONDecodeError:
                    if f'"pid": {pid}' in res.stdout:
                        return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    return False


def run_cli_sample(binary: Path, args: list[str], perf_events: list[str]) -> SampleMetrics:
    t0 = time.perf_counter_ns()
    proc = subprocess.Popen(
        [str(binary), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _ACTIVE_PGIDS.add(proc.pid)
    try:
        _, status, rusage = os.wait4(proc.pid, 0)
        t1 = time.perf_counter_ns()
        proc.returncode = os.waitstatus_to_exitcode(status)
    finally:
        _ACTIVE_PGIDS.discard(proc.pid)

    sample = SampleMetrics(
        wall_ms=(t1 - t0) / 1e6,
        user_cpu_ms=rusage.ru_utime * 1000.0,
        sys_cpu_ms=rusage.ru_stime * 1000.0,
        max_rss_kib=rusage.ru_maxrss,
        min_flt=rusage.ru_minflt,
        maj_flt=rusage.ru_majflt,
        vol_ctxt=rusage.ru_nvcsw,
        invol_ctxt=rusage.ru_nivcsw,
    )

    if perf_events:
        perf_bin = shutil.which("perf")
        if perf_bin is not None:
            p_proc = subprocess.Popen(
                [perf_bin, "stat", "-x", ";", "-e", ",".join(perf_events), "--", str(binary), *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                env=_clean_c_env(),
                start_new_session=True,
            )
            _ACTIVE_PGIDS.add(p_proc.pid)
            try:
                _, p_stderr = p_proc.communicate()
                parse_perf_csv(p_stderr, sample)
            finally:
                _ACTIVE_PGIDS.discard(p_proc.pid)
    return sample


def run_gui_sample(
    binary: Path,
    args: list[str],
    perf_events: list[str],
    settle_ms: float = 50.0,
    timeout_s: float = 5.0,
) -> SampleMetrics:
    t0 = time.perf_counter_ns()
    proc = subprocess.Popen(
        [str(binary), *args],
        cwd=binary.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _ACTIVE_PGIDS.add(proc.pid)

    surface_ready_ns = 0
    gui_mapped = False
    early_rusage: Any = None
    early_exit_ns = 0
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        try:
            pid_done, status, rusage_done = os.wait4(proc.pid, os.WNOHANG)
            if pid_done == proc.pid:
                proc.returncode = os.waitstatus_to_exitcode(status)
                early_rusage = rusage_done
                early_exit_ns = time.perf_counter_ns()
                _ACTIVE_PGIDS.discard(proc.pid)
                break
        except ChildProcessError:
            early_exit_ns = time.perf_counter_ns()
            _ACTIVE_PGIDS.discard(proc.pid)
            break

        if check_hyprland_surface_mapped(proc.pid):
            surface_ready_ns = time.perf_counter_ns()
            gui_mapped = True
            break
        time.sleep(0.008)

    if early_exit_ns != 0:
        surface_ready_ns = early_exit_ns
        t_dismiss_start = early_exit_ns
        t_dismiss_end = early_exit_ns
        vm_hwm_kib, pss_kib = 0, 0
        rusage = early_rusage
    else:
        if surface_ready_ns == 0:
            surface_ready_ns = time.perf_counter_ns()
        if settle_ms > 0.0:
            time.sleep(settle_ms / 1000.0)
        vm_hwm_kib, pss_kib = read_proc_memory_kib(proc.pid)

        t_dismiss_start = time.perf_counter_ns()
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        rusage = reap_popen(proc, timeout_s=2.0)
        t_dismiss_end = time.perf_counter_ns()

    sample = SampleMetrics(
        wall_ms=(t_dismiss_end - t0) / 1e6,
        gui_ready_ms=(surface_ready_ns - t0) / 1e6,
        gui_teardown_ms=(t_dismiss_end - t_dismiss_start) / 1e6,
        gui_mapped=gui_mapped,
        user_cpu_ms=rusage.ru_utime * 1000.0 if rusage else 0.0,
        sys_cpu_ms=rusage.ru_stime * 1000.0 if rusage else 0.0,
        max_rss_kib=max(vm_hwm_kib, rusage.ru_maxrss if rusage else 0),
        pss_kib=pss_kib,
        min_flt=rusage.ru_minflt if rusage else 0,
        maj_flt=rusage.ru_majflt if rusage else 0,
        vol_ctxt=rusage.ru_nvcsw if rusage else 0,
        invol_ctxt=rusage.ru_nivcsw if rusage else 0,
    )

    if perf_events:
        perf_bin = shutil.which("perf")
        if perf_bin is not None:
            p_proc = subprocess.Popen(
                [perf_bin, "stat", "-x", ";", "-e", ",".join(perf_events), "--", str(binary), *args],
                cwd=binary.parent,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                env=_clean_c_env(),
                start_new_session=True,
            )
            _ACTIVE_PGIDS.add(p_proc.pid)
            try:
                wait_window_s = max(0.02, ( sample.gui_ready_ms + settle_ms ) / 1000.0)
                time.sleep(min(wait_window_s, timeout_s))
                subprocess.run(["pkill", "-TERM", "-P", str(p_proc.pid)], check=False)
                try:
                    _, p_stderr = p_proc.communicate(timeout=2.5)
                    parse_perf_csv(p_stderr, sample)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(p_proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        p_proc.kill()
                    _, p_stderr = p_proc.communicate()
                    parse_perf_csv(p_stderr, sample)
            finally:
                try:
                    os.killpg(p_proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
                _ACTIVE_PGIDS.discard(p_proc.pid)

    return sample


# ---------------------------------------------------------------------------
# History Storage & Comparison (Atomic File Management)
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def history_lock() -> Iterator[None]:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with (HISTORY_DIR / ".lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as s:
        tmp = Path(s.name)
        try:
            json.dump(data, s, indent=2)
            s.flush()
            os.fsync(s.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    tmp.replace(path)


def load_history() -> list[dict[str, Any]]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return []


def save_run_record(entry: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        with history_lock():
            history = load_history()
            history.append(entry)
            if len(history) > MAX_HISTORY_ENTRIES:
                evicted = history[:-MAX_HISTORY_ENTRIES]
                retained = history[-MAX_HISTORY_ENTRIES:]
                for old in evicted:
                    old_id = old.get("id")
                    if isinstance(old_id, str) and old_id.startswith("run_"):
                        (HISTORY_DIR / f"{old_id}.json").unlink(missing_ok=True)
            else:
                retained = history
            atomic_json(HISTORY_FILE, retained)
            atomic_json(HISTORY_DIR / f"{entry['id']}.json", entry)
            return retained
    except (OSError, ValueError) as err:
        print(f"[WARN] Failed to write history: {err}", file=sys.stderr)
        return []


def clear_history() -> None:
    with history_lock():
        for p in HISTORY_DIR.glob("run_*.json"):
            p.unlink(missing_ok=True)
        HISTORY_FILE.unlink(missing_ok=True)
    print(f"[OK] Cleared benchmark history in {HISTORY_DIR}")


# ---------------------------------------------------------------------------
# Rich & Terminal Table Rendering
# ---------------------------------------------------------------------------


_ISA_METRICS_SCHEMA: tuple[tuple[str, str, str, bool], ...] = (
    ("size_bytes", "Binary Footprint Size", "Bytes", False),
    ("total_insns", "Total Disassembled Insns", "insn", False),
    ("avx512_evex_insns", "AVX-512 / EVEX Instructions", "insn", True),
    ("avx_vex_insns", "AVX / AVX2 VEX Instructions", "insn", True),
    ("avx_vnni_insns", "AVX-VNNI Neural/Dot-Product", "insn", True),
    ("fma3_insns", "FMA3 Fused Multiply-Add", "insn", True),
    ("bmi1_bmi2_insns", "BMI1 / BMI2 Bit Manipulation", "insn", True),
    ("bitmanip_cnt_insns", "Bit Count & Endian (LZ/TZ/POPCNT/MOVBE)", "insn", True),
    ("crypto_hash_insns", "Crypto & Hash (VAES/AES/SHA/PCLMUL/CRC)", "insn", True),
    ("cache_nt_insns", "Cache & Non-Temporal (MOVNT/PREFETCH)", "insn", True),
)


def render_banner(
    native: Path,
    generic: Path | None,
    args: list[str],
    runs: int,
    warmup: int,
    affinity: set[int] | None,
    perf_events: list[str],
    gui_mode: bool,
) -> None:
    if not RICH_AVAILABLE or console is None:
        print("=" * 118)
        print(" MICROARCHITECTURAL & PERFORMANCE BINARY BENCHMARK")
        print("=" * 118)
        print(f"Target Primary : {native}")
        if generic:
            print(f"Target Baseline: {generic}")
        print(f"Arguments      : {args if args else '(None)'}")
        print(f"Mode           : {'Wayland GUI Lifecycle' if gui_mode else 'Direct CLI Fast-Path'}")
        print(f"Iterations     : {warmup} warm-up, {runs} measured runs")
        print(
            f"CPU Affinity   : {'Pinned to P-cores ' + str(sorted(affinity)) if affinity else 'Unpinned (OS Hybrid Scheduler)'}"
        )
        print(f"Perf PMU       : {', '.join(perf_events) if perf_events else 'Disabled (kernel rusage)'}")
        return

    text = Text()
    text.append("• Target Primary : ", style="bold bright_cyan")
    text.append(f"{native.name} ({native})\n", style="bold white")
    if generic:
        text.append("• Target Baseline: ", style="bold bright_yellow")
        text.append(f"{generic.name} ({generic})\n", style="bold white")
    text.append("• Arguments      : ", style="bold bright_cyan")
    text.append(f"{' '.join(args) if args else '(None)'}\n", style="bright_white")
    text.append("• Execution Mode : ", style="bold bright_cyan")
    text.append(
        f"{'Wayland GUI Lifecycle (Hyprland Socket Polling)' if gui_mode else 'Direct CLI Fast-Path'} "
        f"({warmup} warm-up, {runs} measured)\n",
        style="bright_white",
    )
    text.append("• CPU & Governor : ", style="bold bright_cyan")
    text.append(f"{get_cpu_model()} | {get_scaling_governor()}\n", style="bright_white")
    text.append("• Topology / Pin : ", style="bold bright_cyan")
    text.append(
        f"{'Pinned to Golden Cove P-cores ' + str(sorted(affinity)) if affinity else 'Unpinned (OS Hybrid Scheduler)'}\n",
        style="bright_white",
    )
    text.append("• Hardware PMU   : ", style="bold bright_cyan")
    text.append(
        f"{', '.join(perf_events) if perf_events else 'Disabled (kernel rusage only)'}",
        style="bright_white",
    )

    title = "[bold bright_white]MICROARCHITECTURAL & PERFORMANCE BENCHMARK[/bold bright_white]"
    panel = Panel(text, title=title, border_style="bright_cyan", box=box.ROUNDED)
    console.print(panel)


def render_single_isa(isa: dict[str, Any], name: str) -> None:
    if not isa or not isa.get("total_insns"):
        return
    tot = max(1, int(isa.get("total_insns", 1)))

    if not RICH_AVAILABLE or console is None:
        print(f"\n### Static Disassembly & ISA Breakdown: {name} (SHA256: {isa.get('sha256', '?')})")
        print(f"| {'ISA Metric':<42} | {'Count / Value':<22} | {'Share of Insns':<16} |")
        print(f"|{'-'*44}|{'-'*24}|{'-'*18}|")
        for key, label, unit, is_feat in _ISA_METRICS_SCHEMA:
            val = int(isa.get(key, 0))
            share = f"{(val / tot * 100.0):6.2f}%" if is_feat else "—"
            print(f"| {label:<42} | {val:16,d} {unit:<5} | {share:<16} |")
        return

    table = Table(
        title=f"[bold bright_cyan]Static Binary ISA Breakdown: {name} (SHA256: {isa.get('sha256', '?')})[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        border_style="bright_cyan",
        expand=True,
    )
    table.add_column("Instruction Set / Metric", min_width=36, ratio=1, no_wrap=True)
    table.add_column("Count / Value", justify="right", style="bold bright_white")
    table.add_column("Density (% of Total Insns)", justify="right", min_width=18)

    for idx, (key, label, unit, is_feat) in enumerate(_ISA_METRICS_SCHEMA):
        if idx == 2:
            table.add_section()
        val = int(isa.get(key, 0))
        if not is_feat:
            share_str = "[dim]—[/dim]"
        elif val == 0:
            share_str = "[dim]0.00%[/dim]"
        else:
            share_str = f"[bold bright_green]{(val / tot * 100.0):.2f}%[/bold bright_green]"
        table.add_row(label, f"{val:,d} {unit}", share_str)

    console.print(table)


def render_isa_comparison(n_isa: dict[str, Any], g_isa: dict[str, Any], n_name: str, g_name: str) -> None:
    if not n_isa or not g_isa:
        return

    if not RICH_AVAILABLE or console is None:
        print("\n### Static Disassembly & ISA Audit (objdump -d)")
        print(f"| {'Metric':<40} | {n_name:<22} | {g_name:<22} | {'Delta':<20} |")
        print(f"|{'-'*42}|{'-'*24}|{'-'*24}|{'-'*22}|")
        for key, label, unit, _ in _ISA_METRICS_SCHEMA:
            nv, gv = int(n_isa.get(key, 0)), int(g_isa.get(key, 0))
            diff = nv - gv
            pct = f"({diff / gv * 100.0:+.1f}%)" if gv > 0 and diff != 0 else ""
            print(f"| {label:<40} | {nv:16,d} {unit:<5} | {gv:16,d} {unit:<5} | {diff:+11,d} {pct:<8} |")
        return

    table = Table(
        title="[bold bright_cyan]Static Disassembly & Instruction Set Architecture (ISA) Audit[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        border_style="bright_cyan",
        expand=True,
    )
    table.add_column("ISA Category / Metric", min_width=36, ratio=1, no_wrap=True)
    table.add_column(f"Primary ({n_name})", justify="right", style="bold bright_white")
    table.add_column(f"Baseline ({g_name})", justify="right", style="bright_white")
    table.add_column("Delta (Δ Count & %)", justify="right", min_width=20)

    for idx, (key, label, unit, is_feature) in enumerate(_ISA_METRICS_SCHEMA):
        if idx == 2:
            table.add_section()
        nv, gv = int(n_isa.get(key, 0)), int(g_isa.get(key, 0))
        diff = nv - gv
        pct_str = f" ({diff / gv * 100.0:+.1f}%)" if gv > 0 else (" (new)" if nv > 0 else "")
        if diff == 0:
            diff_str = "[dim white]0 —[/dim white]"
        elif is_feature:
            if diff > 0:
                diff_str = f"[bold bright_green]+{diff:,d}{pct_str} ▲[/bold bright_green]"
            else:
                diff_str = f"[bold bright_red]{diff:,d}{pct_str} ▼[/bold bright_red]"
        else:
            diff_str = f"[bright_yellow]{diff:+,d}{pct_str}[/bright_yellow]"
        table.add_row(label, f"{nv:,d} {unit}", f"{gv:,d} {unit}", diff_str)

    console.print(table)


def _build_metric_sections(
    samples_a: list[SampleMetrics],
    samples_b: list[SampleMetrics] | None,
    gui_mode: bool,
    perf_events: list[str],
) -> list[tuple[str, list[tuple[str, str, Callable[[SampleMetrics], float], bool]]]]:
    all_samples = samples_a + (samples_b if samples_b else [])

    latency_rows: list[tuple[str, str, Callable[[SampleMetrics], float], bool]] = [
        ("Wall Clock Latency", "ms", lambda s: s.wall_ms, True),
    ]
    if gui_mode:
        latency_rows.extend([
            ("GUI Surface Mapped", "ms", lambda s: s.gui_ready_ms, True),
            ("GUI Teardown Latency", "ms", lambda s: s.gui_teardown_ms, True),
        ])
    if perf_events:
        latency_rows.append(("Task Clock Time", "ms", lambda s: s.task_clock_ms, True))

    compute_rows: list[tuple[str, str, Callable[[SampleMetrics], float], bool]] = [
        ("User CPU Time", "ms", lambda s: s.user_cpu_ms, True),
        ("Kernel / System CPU Time", "ms", lambda s: s.sys_cpu_ms, True),
    ]
    if perf_events:
        compute_rows.extend([
            ("P-Core CPU Cycles", "K", lambda s: s.pcore_cycles / 1e3, True),
            ("Retired Instructions", "K", lambda s: s.pcore_instructions / 1e3, True),
            ("Instructions Per Cycle (IPC)", "insn/cyc", sample_ipc, False),
        ])
        if any(s.cache_refs > 0 for s in all_samples):
            compute_rows.append(("Cache References", "K", lambda s: s.cache_refs / 1e3, True))
        if any(s.ecore_cycles > 0 or s.ecore_instructions > 0 for s in all_samples):
            compute_rows.extend([
                ("E-Core CPU Cycles", "K", lambda s: s.ecore_cycles / 1e3, True),
                ("E-Core Instructions", "K", lambda s: s.ecore_instructions / 1e3, True),
            ])

    memory_rows: list[tuple[str, str, Callable[[SampleMetrics], float], bool]] = [
        ("Peak RSS Memory", "KiB", lambda s: float(s.max_rss_kib), True),
    ]
    if gui_mode or any(s.pss_kib > 0 for s in all_samples):
        memory_rows.append(("Proportional Set Size (PSS)", "KiB", lambda s: float(s.pss_kib), True))

    sched_rows: list[tuple[str, str, Callable[[SampleMetrics], float], bool]] = [
        ("Minor Page Faults", "flt", lambda s: float(s.min_flt), True),
        ("Major Page Faults", "flt", lambda s: float(s.maj_flt), True),
        ("Voluntary Context Switches", "ctx", lambda s: float(s.vol_ctxt), True),
        ("Involuntary Context Switches", "ctx", lambda s: float(s.invol_ctxt), True),
    ]

    return [
        ("Latency & Lifecycle", latency_rows),
        ("CPU Compute & PMU", compute_rows),
        ("Memory Footprint", memory_rows),
        ("Kernel Scheduling & Faults", sched_rows),
    ]


def render_comparison_table(
    primary_samples: list[SampleMetrics],
    baseline_samples: list[SampleMetrics],
    p_name: str,
    b_name: str,
    gui_mode: bool,
    perf_events: list[str],
) -> None:
    sections = _build_metric_sections(primary_samples, baseline_samples, gui_mode, perf_events)
    low_sample = len(primary_samples) < 5 or len(baseline_samples) < 5

    if not RICH_AVAILABLE or console is None:
        print(f"\n### Dynamic Execution Benchmark ({len(primary_samples)} ABBA Iterations)")
        header = f"| {'Category / Metric':<30} | {p_name:<42} | {b_name:<42} | {'Delta%':<8} | {'p-val':<9} |"
        sep = f"|{'-'*32}|{'-'*44}|{'-'*44}|{'-'*10}|{'-'*11}|"
        print(header)
        print(sep)
        for sec_title, rows in sections:
            print(f"| **[{sec_title}]** {'':<15} | {'':<42} | {'':<42} | {'':<8} | {'':<9} |")
            for label, unit, extractor, lower_better in rows:
                p_vals = [extractor(x) for x in primary_samples]
                b_vals = [extractor(x) for x in baseline_samples]
                ps, bs = compute_stats(p_vals), compute_stats(b_vals)
                pval = mann_whitney_u_pvalue(p_vals, b_vals)
                delta = ((ps.mean - bs.mean) / bs.mean * 100.0) if bs.mean != 0.0 else 0.0
                if low_sample:
                    p_col = "N/A(n<5)"
                else:
                    sig = "*" if pval < 0.05 else " "
                    p_col = f"{pval:6.4f}{sig}"
                print(
                    f"| {label:<30} | "
                    f"{ps.mean:7.2f} ± {ps.stdev:4.2f} (med {ps.median:6.2f}, p95 {ps.p95:6.2f}) {unit:<4} | "
                    f"{bs.mean:7.2f} ± {bs.stdev:4.2f} (med {bs.median:6.2f}, p95 {bs.p95:6.2f}) {unit:<4} | "
                    f"{delta:+7.2f}% | {p_col:<9} |"
                )
        return

    table = Table(
        title=f"[bold bright_cyan]Dynamic Benchmark: Side-by-Side Comparison ({len(primary_samples)} ABBA Runs)[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        border_style="bright_cyan",
        expand=True,
    )
    table.add_column("Telemetry Metric", min_width=28, ratio=1, no_wrap=True)
    table.add_column(f"Primary ({p_name})", justify="right", style="bold bright_white")
    table.add_column(f"Baseline ({b_name})", justify="right", style="bright_white")
    table.add_column("Δ Relative", justify="right", min_width=12)
    table.add_column("Confidence (p)", justify="right", min_width=12)

    for sec_idx, (sec_title, rows) in enumerate(sections):
        if sec_idx > 0:
            table.add_section()
        table.add_row(
            f"[bold bright_cyan]▸ {sec_title}[/bold bright_cyan]",
            "",
            "",
            "",
            "",
        )
        for label, unit, extractor, lower_better in rows:
            p_vals = [extractor(x) for x in primary_samples]
            b_vals = [extractor(x) for x in baseline_samples]
            ps, bs = compute_stats(p_vals), compute_stats(b_vals)
            pval = mann_whitney_u_pvalue(p_vals, b_vals)
            delta = ((ps.mean - bs.mean) / bs.mean * 100.0) if bs.mean != 0.0 else 0.0

            p_str = f"{ps.mean:.2f} [dim]±{ps.stdev:.2f} (med {ps.median:.2f})[/dim] {unit}"
            b_str = f"{bs.mean:.2f} [dim]±{bs.stdev:.2f} (med {bs.median:.2f})[/dim] {unit}"

            if abs(delta) < 0.1 or (ps.mean == 0.0 and bs.mean == 0.0):
                d_str = "[dim white]0.0% —[/dim white]"
            else:
                is_good = (delta < 0.0) if lower_better else (delta > 0.0)
                arr = "▼" if delta < 0.0 else "▲"
                if not low_sample and pval < 0.05:
                    col = "bold bright_green" if is_good else "bold bright_red"
                else:
                    col = "green" if is_good else "red"
                d_str = f"[{col}]{delta:+.2f}% {arr}[/{col}]"

            if low_sample:
                pval_str = "[dim]N/A (n<5)[/dim]"
            elif pval < 0.05:
                pval_str = f"[bold bright_yellow]{pval:.4f} ★[/bold bright_yellow]"
            else:
                pval_str = f"[dim]{pval:.4f}[/dim]"

            table.add_row(f"  {label}", p_str, b_str, d_str, pval_str)

    console.print(table)


def render_single_table(
    samples: list[SampleMetrics],
    name: str,
    gui_mode: bool,
    perf_events: list[str],
) -> None:
    sections = _build_metric_sections(samples, None, gui_mode, perf_events)

    if not RICH_AVAILABLE or console is None:
        print(f"\n### Standalone Profiling Report: {name} ({len(samples)} runs)")
        print(f"| {'Metric':<30} | {'Mean ± SD (CV%)':<26} | {'Median (IQR)':<20} | {'Min – Max':<18} | {'P95':<12} |")
        print(f"|{'-'*32}|{'-'*28}|{'-'*22}|{'-'*20}|{'-'*14}|")
        for sec_title, rows in sections:
            print(f"| **[{sec_title}]** {'':<15} | {'':<26} | {'':<20} | {'':<18} | {'':<12} |")
            for label, unit, extractor, _ in rows:
                vals = [extractor(s) for s in samples]
                st = compute_stats(vals)
                print(
                    f"| {label:<30} | "
                    f"{st.mean:7.2f} ± {st.stdev:4.2f} ({st.cv_pct:4.1f}%) {unit:<3} | "
                    f"{st.median:7.2f} (IQR {st.iqr:5.2f}) | "
                    f"{st.min_val:6.2f} – {st.max_val:6.2f} | "
                    f"{st.p95:7.2f} {unit:<4} |"
                )
        return

    table = Table(
        title=f"[bold bright_cyan]Standalone Profiling Telemetry: {name} ({len(samples)} Runs)[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        border_style="bright_cyan",
        expand=True,
    )
    table.add_column("Telemetry Metric", min_width=28, ratio=1, no_wrap=True)
    table.add_column("Mean ± SD (CV%)", justify="right", style="bold bright_white")
    table.add_column("Median (IQR)", justify="right", style="bright_white")
    table.add_column("Min – Max", justify="center", style="white")
    table.add_column("95th % (P95)", justify="right", style="bright_white")

    for sec_idx, (sec_title, rows) in enumerate(sections):
        if sec_idx > 0:
            table.add_section()
        table.add_row(f"[bold bright_cyan]▸ {sec_title}[/bold bright_cyan]", "", "", "", "")
        for label, unit, extractor, _ in rows:
            vals = [extractor(s) for s in samples]
            st = compute_stats(vals)
            mean_str = f"{st.mean:.2f} [dim]±{st.stdev:.2f} ({st.cv_pct:.1f}%)[/dim] {unit}"
            med_str = f"{st.median:.2f} [dim](IQR {st.iqr:.2f})[/dim] {unit}"
            mm_str = f"{st.min_val:.2f} – {st.max_val:.2f}"
            p95_str = f"{st.p95:.2f} {unit}"
            table.add_row(f"  {label}", mean_str, med_str, mm_str, p95_str)

    console.print(table)


def render_verdict_panel(
    primary_samples: list[SampleMetrics],
    baseline_samples: list[SampleMetrics],
    p_name: str,
    b_name: str,
    gui_mode: bool = False,
) -> None:
    low_sample = len(primary_samples) < 5 or len(baseline_samples) < 5

    if gui_mode and any(s.gui_mapped for s in primary_samples + baseline_samples):
        lat_label = "GUI Surface Mapping Latency"
        p_lat = [s.gui_ready_ms for s in primary_samples]
        b_lat = [s.gui_ready_ms for s in baseline_samples]
    else:
        lat_label = "Wall Clock Latency"
        p_lat = [s.wall_ms for s in primary_samples]
        b_lat = [s.wall_ms for s in baseline_samples]

    ps_w, bs_w = compute_stats(p_lat), compute_stats(b_lat)
    pval_w = mann_whitney_u_pvalue(p_lat, b_lat)
    delta_w = ((ps_w.mean - bs_w.mean) / bs_w.mean * 100.0) if bs_w.mean != 0.0 else 0.0

    p_in = [float(s.pcore_instructions) for s in primary_samples]
    b_in = [float(s.pcore_instructions) for s in baseline_samples]
    has_pmu = any(p_in) and any(b_in)
    delta_in = 0.0
    pval_in = 1.0
    delta_ipc = 0.0
    ps_ipc_mean = 0.0
    bs_ipc_mean = 0.0

    if has_pmu:
        ps_in, bs_in = compute_stats(p_in), compute_stats(b_in)
        pval_in = mann_whitney_u_pvalue(p_in, b_in)
        delta_in = ((ps_in.mean - bs_in.mean) / bs_in.mean * 100.0) if bs_in.mean != 0.0 else 0.0

        p_ipc_vals = [sample_ipc(s) for s in primary_samples]
        b_ipc_vals = [sample_ipc(s) for s in baseline_samples]
        ps_ipc, bs_ipc = compute_stats(p_ipc_vals), compute_stats(b_ipc_vals)
        ps_ipc_mean, bs_ipc_mean = ps_ipc.mean, bs_ipc.mean
        delta_ipc = ((ps_ipc.mean - bs_ipc.mean) / bs_ipc.mean * 100.0) if bs_ipc.mean != 0.0 else 0.0

    p_rss = compute_stats([float(s.max_rss_kib) for s in primary_samples]).mean
    b_rss = compute_stats([float(s.max_rss_kib) for s in baseline_samples]).mean
    d_rss = ((p_rss - b_rss) / b_rss * 100.0) if b_rss != 0.0 else 0.0

    p_vctx = compute_stats([float(s.vol_ctxt) for s in primary_samples]).mean

    if not RICH_AVAILABLE or console is None:
        print("\n" + "=" * 118)
        print(" EXECUTIVE VERDICT & MICROARCHITECTURAL INTERPRETATION")
        print("=" * 118)
        p_tag = "N/A (n<5)" if low_sample else f"p={pval_w:.4f}"
        if low_sample or abs(delta_w) < 3.5 or pval_w >= 0.05:
            print(
                f"• {lat_label}: {p_name} is {abs(delta_w):.2f}% {'faster' if delta_w < 0 else 'slower'} "
                f"({ps_w.mean:.2f} ms vs {bs_w.mean:.2f} ms, {p_tag})."
            )
            print("  Verdict: PRACTICALLY IDENTICAL. Variance is within normal OS scheduler / compositor noise.")
        else:
            print(
                f"• {lat_label}: {p_name} is STATISTICALLY {'FASTER' if delta_w < 0 else 'SLOWER'} by {abs(delta_w):.2f}% "
                f"({ps_w.mean:.2f} ms vs {bs_w.mean:.2f} ms, {p_tag})."
            )
        if has_pmu:
            print(
                f"• Compute & IPC: {p_name} retires {abs(delta_in):.2f}% {'fewer' if delta_in < 0 else 'more'} instructions "
                f"(p={pval_in:.4f}) with IPC {ps_ipc_mean:.2f} vs {bs_ipc_mean:.2f} ({delta_ipc:+.2f}%)."
            )
        print(
            f"• Memory & IPC Wait: Peak RSS is {p_rss:.0f} KiB vs {b_rss:.0f} KiB ({d_rss:+.1f}%), "
            f"averaging {p_vctx:.1f} voluntary context switches/run."
        )
        print("=" * 118)
        return

    text = Text()
    p_tag = "N/A (n<5)" if low_sample else f"p={pval_w:.4f}"
    text.append(f"• {lat_label:<28}: ", style="bold bright_cyan")
    if low_sample or abs(delta_w) < 3.5 or pval_w >= 0.05:
        text.append(
            f"{p_name} is {abs(delta_w):.2f}% {'faster' if delta_w < 0 else 'slower'} ",
            style="bold white",
        )
        text.append(f"({ps_w.mean:.2f} ms vs {bs_w.mean:.2f} ms, {p_tag}).\n", style="white")
        text.append("  • Practical Verdict        : ", style="dim")
        text.append("Practically Identical", style="bold bright_yellow")
        text.append(
            ". End-to-end timing is bounded by dynamic loader/IPC/compositor round-trips rather than CPU loops.\n",
            style="white",
        )
    else:
        col = "bold bright_green" if delta_w < 0 else "bold bright_red"
        word = "FASTER" if delta_w < 0 else "SLOWER"
        text.append(f"{p_name} is SIGNIFICANTLY {word} by {abs(delta_w):.2f}% ", style=col)
        text.append(f"({ps_w.mean:.2f} ms vs {bs_w.mean:.2f} ms, {p_tag}).\n", style="white")

    if has_pmu:
        text.append(f"• {'Instruction & IPC Efficiency':<28}: ", style="bold bright_cyan")
        in_col = "bold bright_green" if delta_in < 0 else ("bold bright_red" if delta_in > 0 else "white")
        text.append(
            f"{p_name} retires {abs(delta_in):.2f}% {'fewer' if delta_in < 0 else 'more'} instructions ",
            style=in_col,
        )
        text.append(
            f"(p={pval_in:.4f}) | IPC: {ps_ipc_mean:.2f} vs {bs_ipc_mean:.2f} ({delta_ipc:+.2f}%).\n",
            style="white",
        )

    text.append(f"• {'Memory & Scheduler Profile':<28}: ", style="bold bright_cyan")
    text.append(
        f"Peak RSS {p_rss:,.0f} KiB vs {b_rss:,.0f} KiB ({d_rss:+.1f}%) | "
        f"Avg {p_vctx:.1f} voluntary context switches (I/O & socket waits).",
        style="white",
    )

    panel = Panel(
        text,
        title="[bold bright_white]EXECUTIVE VERDICT & MICROARCHITECTURAL INTERPRETATION[/bold bright_white]",
        border_style="bright_cyan",
        box=box.ROUNDED,
    )
    console.print(panel)


def render_history_list(history: list[dict[str, Any]], count: int = 10) -> None:
    if not history:
        print(f"[INFO] No previous benchmark history found in {HISTORY_DIR}")
        return

    runs = history[-max(1, count) :]
    if not RICH_AVAILABLE or console is None:
        print(f"\n=== BENCHMARK HISTORY (Last {len(runs)} Runs) ===")
        print(
            f"| {'ID':<24} | {'Timestamp':<19} | {'Binary':<22} | {'Wall (ms)':<10} | {'Cycles (K)':<11} | {'IPC':<6} | {'RSS (KiB)':<10} |"
        )
        print(f"|{'-'*26}|{'-'*21}|{'-'*24}|{'-'*12}|{'-'*13}|{'-'*8}|{'-'*12}|")
        for r in runs:
            m = r.get("metrics", {})
            print(
                f"| {r.get('id','?'):<24} | {r.get('timestamp','?'):<19} | {r.get('binary_name','?'):<22} | "
                f"{m.get('wall_ms', 0.0):<10.2f} | {m.get('cycles_k', 0.0):<11.1f} | "
                f"{m.get('ipc', 0.0):<6.2f} | {m.get('max_rss_kib', 0):<10,d} |"
            )
        return

    table = Table(
        title=f"[bold bright_cyan]Historical Benchmark Runs (Stored in {HISTORY_DIR})[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        border_style="bright_cyan",
        expand=True,
    )
    table.add_column("Run ID", style="bold white", min_width=22)
    table.add_column("Timestamp", style="dim", min_width=19)
    table.add_column("Target Binary", style="bold bright_cyan", min_width=18)
    table.add_column("Args", style="white")
    table.add_column("Wall Latency", justify="right", style="bold bright_white")
    table.add_column("P-Core Cycles", justify="right", style="bright_white")
    table.add_column("IPC", justify="right", style="bright_green")
    table.add_column("Peak RSS", justify="right", style="white")

    for r in runs:
        m = r.get("metrics", {})
        wall_str = f"{m.get('wall_ms', 0.0):.2f} ms"
        cyc_str = f"{m.get('cycles_k', 0.0):.1f} K"
        ipc_str = f"{m.get('ipc', 0.0):.2f}"
        rss_str = f"{int(m.get('max_rss_kib', 0)):,d} KiB"
        table.add_row(
            str(r.get("id", "?")),
            str(r.get("timestamp", "?")),
            str(r.get("binary_name", "?")),
            str(r.get("args", "")),
            wall_str,
            cyc_str,
            ipc_str,
            rss_str,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Main Orchestration Loop
# ---------------------------------------------------------------------------


def _run_benchmark_suite(args: argparse.Namespace) -> int:
    if args.clear_history:
        clear_history()
        return 0

    history = load_history()

    if args.history is not None:
        if args.history < 1:
            print("[ERR] --history count must be >= 1", file=sys.stderr)
            return 1
        render_history_list(history, count=args.history)
        return 0

    if args.binary is None:
        print("[ERR] Primary binary path is required unless using --history or --clear-history.", file=sys.stderr)
        return 1

    if args.runs < 1:
        print("[ERR] --runs must be >= 1.", file=sys.stderr)
        return 1
    if args.warmup < 0:
        print("[ERR] --warmup must be >= 0.", file=sys.stderr)
        return 1
    if args.gui_settle < 0.0:
        print("[ERR] --gui-settle must be >= 0.0.", file=sys.stderr)
        return 1
    if args.gui_timeout <= 0.0:
        print("[ERR] --gui-timeout must be > 0.0.", file=sys.stderr)
        return 1

    primary = args.binary.resolve()
    if not primary.is_file() or not os.access(primary, os.X_OK):
        print(f"[ERR] Primary binary not found or not executable: {primary}", file=sys.stderr)
        return 1

    baseline = args.baseline.resolve() if args.baseline else None
    if baseline and (not baseline.is_file() or not os.access(baseline, os.X_OK)):
        print(f"[ERR] Baseline binary not found or not executable: {baseline}", file=sys.stderr)
        return 1

    parsed_args = [] if (args.args == "--help" and args.gui) else shlex.split(args.args)
    p_cores, _ = detect_hybrid_cpus()
    affinity = None if args.allow_hybrid or not p_cores else p_cores

    with scoped_cpu_affinity(affinity):
        perf_events = detect_perf_events(allow_hybrid=args.allow_hybrid)
        render_banner(primary, baseline, parsed_args, args.runs, args.warmup, affinity, perf_events, args.gui)

        # 1. Static ISA Disassembly Audit
        n_isa: dict[str, Any] = {}
        g_isa: dict[str, Any] = {}
        if not args.skip_isa:
            n_isa = analyze_binary_isa(primary)
            if baseline:
                g_isa = analyze_binary_isa(baseline)
                render_isa_comparison(n_isa, g_isa, primary.name, baseline.name)
            else:
                render_single_isa(n_isa, primary.name)

        # 2. Benchmark Execution
        runner: Callable[[Path], SampleMetrics] = (
            (lambda b: run_gui_sample(b, parsed_args, perf_events, args.gui_settle, args.gui_timeout))
            if args.gui
            else (lambda b: run_cli_sample(b, parsed_args, perf_events))
        )

        primary_samples: list[SampleMetrics] = []
        baseline_samples: list[SampleMetrics] = []

        if baseline:
            for _ in range(args.warmup):
                runner(primary)
                runner(baseline)
            for i in range(args.runs):
                if i % 2 == 0:
                    primary_samples.append(runner(primary))
                    baseline_samples.append(runner(baseline))
                else:
                    baseline_samples.append(runner(baseline))
                    primary_samples.append(runner(primary))
        else:
            for _ in range(args.warmup):
                runner(primary)
            for _ in range(args.runs):
                primary_samples.append(runner(primary))

    # 3. Telemetry Results & Historical Persistence
    now_ns = time.time_ns()
    run_id = f"run_{now_ns}"
    now_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ns / 1e9))

    primary_stat_wall = compute_stats([s.wall_ms for s in primary_samples])
    primary_stat_cyc = compute_stats([float(s.pcore_cycles) / 1e3 for s in primary_samples])
    primary_stat_ipc = compute_stats([sample_ipc(s) for s in primary_samples])
    primary_stat_rss = compute_stats([float(s.max_rss_kib) for s in primary_samples])

    entry: dict[str, Any] = {
        "id": run_id,
        "timestamp": now_ts,
        "binary_name": primary.name,
        "binary_path": str(primary),
        "args": " ".join(parsed_args),
        "gui_mode": args.gui,
        "metrics": {
            "wall_ms": round(primary_stat_wall.mean, 2),
            "cycles_k": round(primary_stat_cyc.mean, 1),
            "ipc": round(primary_stat_ipc.mean, 3),
            "max_rss_kib": int(primary_stat_rss.mean),
        },
        "isa": n_isa,
        "samples": [dataclasses.asdict(x) for x in primary_samples],
    }

    if baseline:
        b_stat_wall = compute_stats([s.wall_ms for s in baseline_samples])
        b_stat_cyc = compute_stats([float(s.pcore_cycles) / 1e3 for s in baseline_samples])
        b_stat_ipc = compute_stats([sample_ipc(s) for s in baseline_samples])
        b_stat_rss = compute_stats([float(s.max_rss_kib) for s in baseline_samples])
        entry["baseline"] = {
            "name": baseline.name,
            "path": str(baseline),
            "metrics": {
                "wall_ms": round(b_stat_wall.mean, 2),
                "cycles_k": round(b_stat_cyc.mean, 1),
                "ipc": round(b_stat_ipc.mean, 3),
                "max_rss_kib": int(b_stat_rss.mean),
            },
            "isa": g_isa,
            "samples": [dataclasses.asdict(x) for x in baseline_samples],
        }

    updated_history = save_run_record(entry)
    active_history = updated_history if updated_history else history

    # 4. Rendering Output Tables
    if baseline:
        render_comparison_table(primary_samples, baseline_samples, primary.name, baseline.name, args.gui, perf_events)
        render_verdict_panel(primary_samples, baseline_samples, primary.name, baseline.name, gui_mode=args.gui)
    else:
        render_single_table(primary_samples, primary.name, args.gui, perf_events)

        comp_entry: dict[str, Any] | None = None
        if args.compare:
            for item in active_history:
                if item.get("id") == args.compare:
                    comp_entry = item
                    break
            if not comp_entry:
                candidate_file = HISTORY_DIR / f"{args.compare}.json"
                if candidate_file.is_file():
                    try:
                        comp_entry = json.loads(candidate_file.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        pass
            if not comp_entry:
                print(f"[WARN] Target historical Run ID '{args.compare}' not found.", file=sys.stderr)
        elif args.diff_last:
            matching = [x for x in active_history if x.get("binary_name") == primary.name and x.get("id") != run_id]
            if matching:
                comp_entry = matching[-1]
            else:
                prior = [x for x in active_history if x.get("id") != run_id]
                if prior:
                    comp_entry = prior[-1]

        if comp_entry and isinstance(comp_entry.get("samples"), list):
            hist_samples = [
                SampleMetrics(**{k: v for k, v in d.items() if k in _SAMPLE_FIELD_NAMES})
                for d in comp_entry["samples"]
                if isinstance(d, dict)
            ]
            if hist_samples:
                label_hist = f"Historical ({str(comp_entry.get('id', '?'))[:14]})"
                print()
                render_comparison_table(primary_samples, hist_samples, primary.name, label_hist, args.gui, perf_events)
                render_verdict_panel(primary_samples, hist_samples, primary.name, label_hist, gui_mode=args.gui)

    if args.json:
        atomic_json(args.json.resolve(), entry)
        print(f"[OK] Full benchmark report exported to {args.json}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "binary",
        type=Path,
        nargs="?",
        default=None,
        help="Primary binary (or Native build when comparing two)",
    )
    parser.add_argument(
        "baseline",
        type=Path,
        nargs="?",
        default=None,
        help="Optional secondary binary (Generic baseline) for head-to-head comparison",
    )
    parser.add_argument("-a", "--args", type=str, default="--help", help="Arguments to pass to binaries (default: '--help')")
    parser.add_argument("-n", "--runs", type=int, default=50, help="Number of measured runs (default: 50, min: 1)")
    parser.add_argument("-w", "--warmup", type=int, default=5, help="Number of warmup runs (default: 5, min: 0)")
    parser.add_argument("--gui", action="store_true", help="Enable Wayland GUI lifecycle mode (tracks surface mapping & teardown)")
    parser.add_argument("--gui-settle", type=float, default=50.0, help="Settle time in ms for GUI before dismissal (default: 50.0ms)")
    parser.add_argument("--gui-timeout", type=float, default=5.0, help="Timeout in seconds for GUI mapping (default: 5.0s)")
    parser.add_argument("--allow-hybrid", action="store_true", help="Do not pin execution to Golden Cove P-cores")
    parser.add_argument("-s", "--skip-isa", action="store_true", help="Skip static objdump ISA disassembly audit")
    parser.add_argument("-o", "--json", type=Path, default=None, help="Save complete telemetry report to JSON")
    parser.add_argument("--diff-last", action="store_true", help="Compare single binary against the most recent historical run")
    parser.add_argument("--compare", type=str, default=None, help="Compare single binary against a specific historical Run ID")
    parser.add_argument("--history", type=int, nargs="?", const=10, default=None, help="Display recent historical runs and exit")
    parser.add_argument("--clear-history", action="store_true", help="Clear all stored benchmark history and exit")

    args = parser.parse_args()

    def _handle_sigterm(signum: int, _frame: Any) -> None:
        cleanup_active_pgids()
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    try:
        rc = _run_benchmark_suite(args)
    except (KeyboardInterrupt, BrokenPipeError):
        cleanup_active_pgids()
        sys.exit(130)
    finally:
        cleanup_active_pgids()
    sys.exit(rc)


if __name__ == "__main__":
    main()
