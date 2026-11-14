#!/usr/bin/env python3
"""
native_vs_generic.py - Comprehensive Microarchitectural & Performance Benchmark Tool.
Profiles and compares compiled binaries (Rust, C/C++, Go, etc.) for CPU cycles,
ISA specialization (AVX2, BMI2, FMA), execution latency, and memory footprint.

Supports:
- Single Binary Profiling (records metrics & auto-diffs against historical runs)
- Head-to-Head Comparison (Native vs. Generic with interleaved ABBA execution)
- Fast-Path CLI & Full Wayland GUI Lifecycle testing

Target: Arch Linux | Kernel 7.3+ | Python 3.15+ | Hyprland (Wayland)
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
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

console = Console(width=max(126, shutil.get_terminal_size((126, 24)).columns)) if RICH_AVAILABLE else None

HISTORY_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "dusky"
    / "settings"
    / "binary_compare"
)
HISTORY_FILE = HISTORY_DIR / "history.json"


# ---------------------------------------------------------------------------
# Telemetry Data Structures
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
    task_clock_ms: float = 0.0
    gui_ready_ms: float = 0.0
    gui_teardown_ms: float = 0.0


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
    cv_pct = (stdev / mean * 100.0) if mean != 0 else 0.0
    p95_idx = min(n - 1, max(0, math.ceil(0.95 * n) - 1))
    return StatSummary(
        mean=mean,
        median=median,
        stdev=stdev,
        cv_pct=cv_pct,
        min_val=s[0],
        max_val=s[-1],
        p95=s[p95_idx],
        iqr=s[(3 * n) // 4] - s[n // 4],
    )


def mann_whitney_u_pvalue(x: list[float], y: list[float]) -> float:
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return 1.0
    combined = sorted([(v, 0) for v in x] + [(v, 1) for v in y], key=lambda item: item[0])
    ranks = [0.0] * len(combined)
    tie_sum = 0.0
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
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
    n = n1 + n2
    tie_corr = tie_sum / (n * (n - 1)) if n > 1 else 0.0
    sigma_sq = (n1 * n2 / 12.0) * ((n + 1) - tie_corr)
    if sigma_sq <= 0:
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
        os.sched_setaffinity(0, orig)


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
        epp = Path("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference").read_text(encoding="utf-8").strip()
        return f"{gov} ({epp})"
    except (OSError, ValueError):
        pass
    return "Default"


# ---------------------------------------------------------------------------
# Static ISA & Disassembly Audit (Streaming)
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
        "avx_vex_insns": 0,
        "fma3_insns": 0,
        "bmi1_bmi2_insns": 0,
        "bitmanip_cnt_insns": 0,
    }
    objdump = shutil.which("objdump")
    if not objdump:
        return info

    proc = subprocess.Popen(
        [objdump, "-d", "--no-show-raw-insn", str(binary)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        errors="replace",
    )
    bmi_set = {
        "andn", "bextr", "blsi", "blsmsk", "blsr",
        "bzhi", "mulx", "pdep", "pext", "rorx", "sarx", "shlx", "shrx",
    }
    cnt_set = {"lzcnt", "tzcnt", "popcnt"}
    fma_prefixes = ("vfmadd", "vfmsub", "vfnmadd", "vfnmsub", "vfmaddsub", "vfmsubadd")

    if proc.stdout:
        for line in proc.stdout:
            if not line.startswith(" ") or ":" not in line:
                continue
            _, _, rest = line.partition(":")
            tokens = rest.strip().split()
            if not tokens:
                continue
            m = tokens[0].lower()
            info["total_insns"] += 1
            if m.startswith("v"):
                info["avx_vex_insns"] += 1
            if m.startswith(fma_prefixes):
                info["fma3_insns"] += 1
            if m in bmi_set:
                info["bmi1_bmi2_insns"] += 1
            if m in cnt_set:
                info["bitmanip_cnt_insns"] += 1
    proc.wait()
    return info


# ---------------------------------------------------------------------------
# Hardware PMU Performance Counters (perf stat)
# ---------------------------------------------------------------------------


def detect_perf_events() -> list[str]:
    perf = shutil.which("perf")
    if not perf:
        return []
    events = ["task-clock"]
    if Path("/sys/devices/cpu_core").exists():
        events.extend(["cpu_core/cycles/", "cpu_core/instructions/"])
    else:
        events.extend(["cycles", "instructions"])
    if Path("/sys/devices/cpu_atom").exists():
        events.extend(["cpu_atom/cycles/", "cpu_atom/instructions/"])
    res = subprocess.run([perf, "stat", "-x", ";", "-e", ",".join(events), "--", "true"], capture_output=True, text=True, check=False)
    if res.returncode == 0 and "<not supported>" not in res.stderr:
        return events
    fallback = ["task-clock", "cycles", "instructions"]
    res2 = subprocess.run([perf, "stat", "-x", ";", "-e", ",".join(fallback), "--", "true"], capture_output=True, text=True, check=False)
    return fallback if res2.returncode == 0 else []


def parse_perf_csv(stderr_text: str, sample: SampleMetrics) -> None:
    for line in stderr_text.splitlines():
        parts = line.split(";")
        if len(parts) < 3:
            continue
        raw_val, event_raw = parts[0].strip(), parts[2].strip()
        event_name = event_raw.split(":")[0].rstrip("/")
        if not raw_val or raw_val.startswith("<"):
            continue
        try:
            val = float(raw_val.replace(",", ""))
        except ValueError:
            continue
        if event_name == "task-clock":
            sample.task_clock_ms = val
        elif event_name in ("cpu_core/cycles", "cycles"):
            sample.pcore_cycles = int(val)
        elif event_name in ("cpu_core/instructions", "instructions"):
            sample.pcore_instructions = int(val)
        elif event_name == "cpu_atom/cycles":
            sample.ecore_cycles = int(val)
        elif event_name == "cpu_atom/instructions":
            sample.ecore_instructions = int(val)


# ---------------------------------------------------------------------------
# Process Execution & Telemetry Capture
# ---------------------------------------------------------------------------


def read_proc_memory_kib(pid: int) -> tuple[int, int]:
    vm_hwm, pss = 0, 0
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                vm_hwm = int(line.split()[1])
                break
    except (OSError, ValueError):
        pass
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text(encoding="utf-8").splitlines():
            if line.startswith("Pss:"):
                pss = int(line.split()[1])
                break
    except (OSError, ValueError):
        pass
    return vm_hwm, pss


def reap_popen(proc: subprocess.Popen[Any], timeout_s: float = 2.0) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            pid_done, status, rusage = os.wait4(proc.pid, os.WNOHANG)
            if pid_done == proc.pid:
                proc.returncode = os.waitstatus_to_exitcode(status)
                return rusage
        except ChildProcessError:
            break
        time.sleep(0.0005)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        _, status, rusage = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        return rusage
    except ChildProcessError:
        pass
    return None


def check_hyprland_surface_mapped(pid: int) -> bool:
    hyprctl = shutil.which("hyprctl")
    if not hyprctl or not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return False
    try:
        res = subprocess.run([hyprctl, "clients", "-j"], capture_output=True, text=True, timeout=0.15, check=False)
        if res.returncode == 0 and f'"pid": {pid}' in res.stdout:
            return True
        res_l = subprocess.run([hyprctl, "layers", "-j"], capture_output=True, text=True, timeout=0.15, check=False)
        if res_l.returncode == 0 and f'"pid": {pid}' in res_l.stdout:
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
    _, status, rusage = os.wait4(proc.pid, 0)
    t1 = time.perf_counter_ns()
    proc.returncode = os.waitstatus_to_exitcode(status)

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
        assert perf_bin is not None
        p_res = subprocess.run(
            [perf_bin, "stat", "-x", ";", "-e", ",".join(perf_events), "--", str(binary), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        parse_perf_csv(p_res.stderr, sample)
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

    surface_ready_ns = 0
    deadline = time.monotonic() + timeout_s

    def is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    while time.monotonic() < deadline:
        if not is_alive(proc.pid):
            break
        if check_hyprland_surface_mapped(proc.pid):
            surface_ready_ns = time.perf_counter_ns()
            break
        time.sleep(0.002)

    if surface_ready_ns == 0:
        surface_ready_ns = time.perf_counter_ns()

    time.sleep(settle_ms / 1000.0)
    vm_hwm_kib, pss_kib = read_proc_memory_kib(proc.pid)

    t_dismiss_start = time.perf_counter_ns()
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    rusage = reap_popen(proc, timeout_s=2.0)
    t_dismiss_end = time.perf_counter_ns()

    sample = SampleMetrics(
        wall_ms=(t_dismiss_end - t0) / 1e6,
        gui_ready_ms=(surface_ready_ns - t0) / 1e6,
        gui_teardown_ms=(t_dismiss_end - t_dismiss_start) / 1e6,
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
        assert perf_bin is not None
        p_proc = subprocess.Popen(
            [perf_bin, "stat", "-x", ";", "-e", ",".join(perf_events), "--", str(binary), *args],
            cwd=binary.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        time.sleep(settle_ms / 1000.0 + 0.05)
        subprocess.run(["pkill", "-TERM", "-P", str(p_proc.pid)], check=False)
        try:
            _, p_stderr = p_proc.communicate(timeout=3.0)
            parse_perf_csv(p_stderr, sample)
        except subprocess.TimeoutExpired:
            p_proc.kill()
            p_proc.communicate()

    return sample


# ---------------------------------------------------------------------------
# History Storage & Comparison (Atomic File Management)
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def history_lock() -> Iterator[None]:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with (HISTORY_DIR / ".lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as s:
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
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return []


def save_run_record(entry: dict[str, Any]) -> None:
    try:
        with history_lock():
            history = load_history()
            history.append(entry)
            retained = history[-100:]
            atomic_json(HISTORY_FILE, retained)
            atomic_json(HISTORY_DIR / f"{entry['id']}.json", entry)
    except (OSError, ValueError) as err:
        print(f"[WARN] Failed to write history: {err}", file=sys.stderr)


def clear_history() -> None:
    with history_lock():
        for p in HISTORY_DIR.glob("run_*.json"):
            p.unlink(missing_ok=True)
        HISTORY_FILE.unlink(missing_ok=True)
    print(f"[OK] Cleared benchmark history in {HISTORY_DIR}")


# ---------------------------------------------------------------------------
# Rich & Terminal Table Rendering
# ---------------------------------------------------------------------------


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
        print("=" * 114)
        print(" MICROARCHITECTURAL BINARY BENCHMARK")
        print("=" * 114)
        print(f"Target Primary : {native}")
        if generic:
            print(f"Target Baseline: {generic}")
        print(f"Arguments      : {args if args else '(None)'}")
        print(f"Mode           : {'Wayland GUI Lifecycle' if gui_mode else 'Direct CLI Fast-Path'}")
        print(f"Iterations     : {warmup} warm-up, {runs} measured runs")
        print(f"CPU Affinity   : {'Pinned to P-cores ' + str(sorted(affinity)) if affinity else 'Unpinned (OS Scheduler)'}")
        print(f"Perf PMU       : {', '.join(perf_events) if perf_events else 'Disabled'}")
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
    text.append(f"{'Wayland GUI Lifecycle' if gui_mode else 'Direct CLI Fast-Path'}\n", style="bright_white")
    text.append("• CPU & Governor : ", style="bold bright_cyan")
    text.append(f"{get_cpu_model()} | {get_scaling_governor()}\n", style="bright_white")
    text.append("• Topology / Pin : ", style="bold bright_cyan")
    text.append(f"{'Pinned to P-cores ' + str(sorted(affinity)) if affinity else 'Unpinned (OS Scheduler)'}\n", style="bright_white")
    text.append("• Hardware PMU   : ", style="bold bright_cyan")
    text.append(f"{', '.join(perf_events) if perf_events else 'Disabled (kernel rusage)'}", style="bright_white")

    title = "[bold bright_white]MICROARCHITECTURAL & PERFORMANCE BENCHMARK[/bold bright_white]"
    panel = Panel(text, title=title, border_style="bright_cyan", box=box.ROUNDED)
    console.print(panel)


def render_isa_comparison(n_isa: dict[str, Any], g_isa: dict[str, Any], n_name: str, g_name: str) -> None:
    if not n_isa or not g_isa:
        return

    metrics = [
        ("size_bytes", "Binary Size", "Bytes", False),
        ("total_insns", "Total Disassembled Insns", "insn", False),
        ("avx_vex_insns", "AVX / AVX2 VEX Instructions", "insn", True),
        ("fma3_insns", "FMA3 Fused Multiply-Add", "insn", True),
        ("bmi1_bmi2_insns", "BMI1 / BMI2 Bit Manipulation", "insn", True),
        ("bitmanip_cnt_insns", "LZCNT / TZCNT / POPCNT", "insn", True),
    ]

    if not RICH_AVAILABLE or console is None:
        print("\n### Static Disassembly & ISA Audit (objdump -d)")
        print(f"| {'Metric':<30} | {n_name:<28} | {g_name:<28} | {'Delta':<14} |")
        print(f"|{'-'*32}|{'-'*30}|{'-'*30}|{'-'*16}|")
        for key, label, unit, _ in metrics:
            nv, gv = n_isa[key], g_isa[key]
            diff = nv - gv
            print(f"| {label:<30} | {nv:<28,d} | {gv:<28,d} | {diff:+14,d} |")
        return

    table = Table(
        title="[bold bright_cyan]Static Disassembly & Instruction Set Architecture (ISA) Audit[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        border_style="bright_cyan",
        expand=True,
    )
    table.add_column("ISA Metric", min_width=28, ratio=1, no_wrap=True)
    table.add_column(f"Primary ({n_name})", justify="right", style="bold bright_white")
    table.add_column(f"Baseline ({g_name})", justify="right", style="bright_white")
    table.add_column("Difference", justify="right", min_width=14)

    for key, label, unit, is_feature in metrics:
        nv, gv = n_isa[key], g_isa[key]
        diff = nv - gv
        if diff == 0:
            diff_str = "[dim white]0 —[/dim white]"
        elif is_feature:
            diff_str = f"[bold bright_green]+{diff:,d} ▲[/bold bright_green]" if diff > 0 else f"[bold bright_red]{diff:,d} ▼[/bold bright_red]"
        else:
            diff_str = f"[bright_yellow]{diff:+,d}[/bright_yellow]"
        table.add_row(label, f"{nv:,d} {unit}", f"{gv:,d} {unit}", diff_str)

    console.print(table)


def render_comparison_table(
    primary_samples: list[SampleMetrics],
    baseline_samples: list[SampleMetrics],
    p_name: str,
    b_name: str,
    gui_mode: bool,
    perf_events: list[str],
) -> None:
    rows_meta: list[tuple[str, str, str, Any, bool]] = [
        ("Wall Clock Latency", "ms", "latency", lambda s: s.wall_ms, True),
    ]
    if gui_mode:
        rows_meta.extend([
            ("GUI Surface Mapped", "ms", "latency", lambda s: s.gui_ready_ms, True),
            ("GUI Teardown Latency", "ms", "latency", lambda s: s.gui_teardown_ms, True),
        ])
    rows_meta.extend([
        ("User CPU Time", "ms", "cpu", lambda s: s.user_cpu_ms, True),
        ("Kernel / System CPU Time", "ms", "cpu", lambda s: s.sys_cpu_ms, True),
    ])
    if perf_events:
        rows_meta.extend([
            ("Task Clock", "ms", "latency", lambda s: s.task_clock_ms, True),
            ("P-Core CPU Cycles", "K", "cycles", lambda s: s.pcore_cycles / 1e3, True),
            ("Retired Instructions", "K", "insn", lambda s: s.pcore_instructions / 1e3, True),
        ])
    rows_meta.append(("Peak RSS Memory", "KiB", "memory", lambda s: float(s.max_rss_kib), True))
    if gui_mode:
        rows_meta.append(("Proportional Set (PSS)", "KiB", "memory", lambda s: float(s.pss_kib), True))
    rows_meta.append(("Minor Page Faults", "flt", "faults", lambda s: float(s.min_flt), True))

    if not RICH_AVAILABLE or console is None:
        print(f"\n### Dynamic Execution Benchmark ({len(primary_samples)} ABBA Iterations)")
        header = f"| {'Metric':<28} | {p_name:<42} | {b_name:<42} | {'Delta%':<7} | {'p-val':<7} |"
        sep = f"|{'-'*30}|{'-'*44}|{'-'*44}|{'-'*9}|{'-'*9}|"
        print(header)
        print(sep)
        for label, unit, _, extractor, lower_better in rows_meta:
            p_vals = [extractor(x) for x in primary_samples]
            b_vals = [extractor(x) for x in baseline_samples]
            ps, bs = compute_stats(p_vals), compute_stats(b_vals)
            pval = mann_whitney_u_pvalue(p_vals, b_vals)
            delta = ((ps.mean - bs.mean) / bs.mean * 100.0) if bs.mean != 0 else 0.0
            sig = "*" if pval < 0.05 else " "
            print(
                f"| {label:<28} | "
                f"{ps.mean:7.2f} ± {ps.stdev:4.2f} (med {ps.median:6.2f}, p95 {ps.p95:6.2f}) {unit:<4} | "
                f"{bs.mean:7.2f} ± {bs.stdev:4.2f} (med {bs.median:6.2f}, p95 {bs.p95:6.2f}) {unit:<4} | "
                f"{delta:+6.2f}% | {pval:6.4f}{sig} |"
            )
        return

    table = Table(
        title=f"[bold bright_cyan]Dynamic Benchmark: Side-by-Side Comparison ({len(primary_samples)} ABBA Runs)[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        border_style="bright_cyan",
        expand=True,
    )
    table.add_column("Performance Metric", min_width=24, ratio=1, no_wrap=True)
    table.add_column(f"Primary ({p_name})", justify="right", style="bold bright_white")
    table.add_column(f"Baseline ({b_name})", justify="right", style="bright_white")
    table.add_column("Δ Relative", justify="right", min_width=10)
    table.add_column("p-value", justify="right", min_width=8)

    for label, unit, mode, extractor, lower_better in rows_meta:
        p_vals = [extractor(x) for x in primary_samples]
        b_vals = [extractor(x) for x in baseline_samples]
        ps, bs = compute_stats(p_vals), compute_stats(b_vals)
        pval = mann_whitney_u_pvalue(p_vals, b_vals)
        delta = ((ps.mean - bs.mean) / bs.mean * 100.0) if bs.mean != 0 else 0.0

        p_str = f"{ps.mean:.2f} [dim]±{ps.stdev:.2f}[/dim] (med {ps.median:.2f}) {unit}"
        b_str = f"{bs.mean:.2f} [dim]±{bs.stdev:.2f}[/dim] (med {bs.median:.2f}) {unit}"

        if abs(delta) < 0.1:
            d_str = "[dim white]0.0% —[/dim white]"
        else:
            is_good = (delta < 0) if lower_better else (delta > 0)
            col = "bold bright_green" if is_good else "bold bright_red"
            arr = "▼" if delta < 0 else "▲"
            d_str = f"[{col}]{delta:+.2f}% {arr}[/{col}]"

        pval_str = f"[bold bright_yellow]{pval:.4f}*[/bold bright_yellow]" if pval < 0.05 else f"[dim]{pval:.4f}[/dim]"
        table.add_row(label, p_str, b_str, d_str, pval_str)

    console.print(table)


def render_single_table(samples: list[SampleMetrics], name: str, gui_mode: bool, perf_events: list[str]) -> None:
    rows = [
        ("Wall Clock Latency", "ms", [s.wall_ms for s in samples]),
    ]
    if gui_mode:
        rows.extend([
            ("GUI Surface Mapped", "ms", [s.gui_ready_ms for s in samples]),
            ("GUI Teardown Latency", "ms", [s.gui_teardown_ms for s in samples]),
        ])
    rows.extend([
        ("User CPU Time", "ms", [s.user_cpu_ms for s in samples]),
        ("Kernel / System CPU Time", "ms", [s.sys_cpu_ms for s in samples]),
    ])
    if perf_events:
        rows.extend([
            ("Task Clock Time", "ms", [s.task_clock_ms for s in samples]),
            ("P-Core CPU Cycles", "K", [s.pcore_cycles / 1e3 for s in samples]),
            ("Retired Instructions", "K", [s.pcore_instructions / 1e3 for s in samples]),
        ])
    rows.append(("Peak RSS Memory", "KiB", [float(s.max_rss_kib) for s in samples]))
    if gui_mode:
        rows.append(("Proportional Set (PSS)", "KiB", [float(s.pss_kib) for s in samples]))
    rows.append(("Minor Page Faults", "flt", [float(s.min_flt) for s in samples]))

    if not RICH_AVAILABLE or console is None:
        print(f"\n### Standalone Profiling Report: {name} ({len(samples)} runs)")
        print(f"| {'Metric':<28} | {'Mean ± SD':<22} | {'Median':<12} | {'Min – Max':<18} | {'P95':<12} |")
        print(f"|{'-'*30}|{'-'*24}|{'-'*14}|{'-'*20}|{'-'*14}|")
        for label, unit, vals in rows:
            st = compute_stats(vals)
            print(f"| {label:<28} | {st.mean:7.2f} ± {st.stdev:4.2f} {unit:<4} | {st.median:7.2f} {unit:<4} | {st.min_val:6.2f} – {st.max_val:6.2f} | {st.p95:7.2f} {unit:<4} |")
        return

    table = Table(
        title=f"[bold bright_cyan]Standalone Profiling Telemetry: {name} ({len(samples)} Runs)[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        border_style="bright_cyan",
        expand=True,
    )
    table.add_column("Metric", min_width=24, ratio=1, no_wrap=True)
    table.add_column("Mean ± SD", justify="right", style="bold bright_white")
    table.add_column("Median (P50)", justify="right", style="bright_white")
    table.add_column("Min – Max", justify="center", style="white")
    table.add_column("95th % (P95)", justify="right", style="bright_white")

    for label, unit, vals in rows:
        st = compute_stats(vals)
        mean_str = f"{st.mean:.2f} [dim]±{st.stdev:.2f}[/dim] {unit}"
        med_str = f"{st.median:.2f} {unit}"
        mm_str = f"{st.min_val:.1f} – {st.max_val:.1f}"
        p95_str = f"{st.p95:.2f} {unit}"
        table.add_row(label, mean_str, med_str, mm_str, p95_str)

    console.print(table)


def render_verdict_panel(
    primary_samples: list[SampleMetrics],
    baseline_samples: list[SampleMetrics],
    p_name: str,
    b_name: str,
) -> None:
    p_wall = [s.wall_ms for s in primary_samples]
    b_wall = [s.wall_ms for s in baseline_samples]
    ps_w, bs_w = compute_stats(p_wall), compute_stats(b_wall)
    pval_w = mann_whitney_u_pvalue(p_wall, b_wall)
    delta_w = ((ps_w.mean - bs_w.mean) / bs_w.mean * 100.0) if bs_w.mean != 0 else 0.0

    p_in = [float(s.pcore_instructions) for s in primary_samples]
    b_in = [float(s.pcore_instructions) for s in baseline_samples]
    delta_in = 0.0
    pval_in = 1.0
    if any(p_in) and any(b_in):
        ps_in, bs_in = compute_stats(p_in), compute_stats(b_in)
        pval_in = mann_whitney_u_pvalue(p_in, b_in)
        delta_in = ((ps_in.mean - bs_in.mean) / bs_in.mean * 100.0) if bs_in.mean != 0 else 0.0

    if not RICH_AVAILABLE or console is None:
        print("\n" + "=" * 114)
        print(" VERDICT & INTERPRETATION")
        print("=" * 114)
        if abs(delta_w) < 4.0 or pval_w >= 0.05:
            print(f"• Latency: {p_name} is {abs(delta_w):.2f}% {'faster' if delta_w < 0 else 'slower'} ({ps_w.mean:.2f}ms vs {bs_w.mean:.2f}ms, p={pval_w:.4f}).")
            print("           The difference is MINOR / PRACTICALLY IDENTICAL in real-world usage.")
        else:
            print(f"• Latency: {p_name} is SIGNIFICANTLY {'faster' if delta_w < 0 else 'slower'} by {abs(delta_w):.2f}% ({ps_w.mean:.2f}ms vs {bs_w.mean:.2f}ms, p={pval_w:.4f}).")
        if delta_in != 0.0:
            print(f"• Compute: {p_name} executes {abs(delta_in):.2f}% {'fewer' if delta_in < 0 else 'more'} CPU instructions (p={pval_in:.4f}).")
        print("=" * 114)
        return

    text = Text()
    if abs(delta_w) < 4.0 or pval_w >= 0.05:
        text.append("• Execution Latency: ", style="bold bright_cyan")
        text.append(f"{p_name} is {abs(delta_w):.2f}% {'faster' if delta_w < 0 else 'slower'} ", style="bold white")
        text.append(f"({ps_w.mean:.2f} ms vs {bs_w.mean:.2f} ms, p={pval_w:.4f}).\n", style="white")
        text.append("  [dim]Result:[/dim] [bold bright_yellow]Practically Identical[/bold bright_yellow]. The timing variance is within ordinary scheduler margins.\n", style="white")
    else:
        text.append("• Execution Latency: ", style="bold bright_cyan")
        text.append(f"{p_name} is SIGNIFICANTLY {'faster' if delta_w < 0 else 'slower'} by {abs(delta_w):.2f}% ", style="bold bright_green" if delta_w < 0 else "bold bright_red")
        text.append(f"({ps_w.mean:.2f} ms vs {bs_w.mean:.2f} ms, p={pval_w:.4f}).\n", style="white")

    if delta_in != 0.0:
        text.append("• Instruction Load : ", style="bold bright_cyan")
        text.append(f"{p_name} retires {abs(delta_in):.2f}% {'fewer' if delta_in < 0 else 'more'} CPU instructions ", style="bold bright_green" if delta_in < 0 else "bold bright_red")
        text.append(f"due to compiler vectorization (p={pval_in:.4f}).\n", style="white")

    p_rss = compute_stats([float(s.max_rss_kib) for s in primary_samples]).mean
    b_rss = compute_stats([float(s.max_rss_kib) for s in baseline_samples]).mean
    d_rss = ((p_rss - b_rss) / b_rss * 100.0) if b_rss != 0 else 0.0
    text.append("• RAM Consumption  : ", style="bold bright_cyan")
    text.append(f"{p_name} uses {abs(d_rss):.1f}% {'less' if d_rss < 0 else 'more'} Peak RSS ({p_rss:.0f} KiB vs {b_rss:.0f} KiB).", style="white")

    panel = Panel(text, title="[bold bright_white]EXECUTIVE VERDICT & INTERPRETATION[/bold bright_white]", border_style="bright_cyan", box=box.ROUNDED)
    console.print(panel)


def render_history_list(history: list[dict[str, Any]], count: int = 10) -> None:
    if not history:
        print(f"[INFO] No previous benchmark history found in {HISTORY_DIR}")
        return

    runs = history[-count:]
    if not RICH_AVAILABLE or console is None:
        print(f"\n=== BENCHMARK HISTORY (Last {len(runs)} Runs) ===")
        print(f"| {'ID':<16} | {'Timestamp':<20} | {'Binary':<24} | {'Wall (ms)':<10} | {'Cycles (K)':<12} | {'RSS (KiB)':<10} |")
        print(f"|{'-'*18}|{'-'*22}|{'-'*26}|{'-'*12}|{'-'*14}|{'-'*12}|")
        for r in runs:
            m = r.get("metrics", {})
            print(f"| {r.get('id','?'):<16} | {r.get('timestamp','?'):<20} | {r.get('binary_name','?'):<24} | {m.get('wall_ms', 0.0):<10.2f} | {m.get('cycles_k', 0.0):<12.1f} | {m.get('max_rss_kib', 0):<10} |")
        return

    table = Table(
        title=f"[bold bright_cyan]Historical Benchmark Runs (Stored in {HISTORY_DIR})[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        border_style="bright_cyan",
        expand=True,
    )
    table.add_column("Run ID", style="bold white", min_width=16)
    table.add_column("Timestamp", style="dim", min_width=19)
    table.add_column("Target Binary", style="bold bright_cyan", min_width=20)
    table.add_column("Args", style="white")
    table.add_column("Wall Latency", justify="right", style="bold bright_white")
    table.add_column("P-Core Cycles", justify="right", style="bright_white")
    table.add_column("Peak RSS", justify="right", style="white")

    for r in runs:
        m = r.get("metrics", {})
        wall_str = f"{m.get('wall_ms', 0.0):.2f} ms"
        cyc_str = f"{m.get('cycles_k', 0.0):.1f} K"
        rss_str = f"{m.get('max_rss_kib', 0):,d} KiB"
        table.add_row(r.get("id", "?"), r.get("timestamp", "?"), r.get("binary_name", "?"), r.get("args", ""), wall_str, cyc_str, rss_str)

    console.print(table)


# ---------------------------------------------------------------------------
# Main Orchestration Loop
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("binary", type=Path, help="Primary binary (or Native build when comparing two)")
    parser.add_argument("baseline", type=Path, nargs="?", default=None, help="Optional secondary binary (Generic baseline) for head-to-head comparison")
    parser.add_argument("-a", "--args", type=str, default="--help", help="Arguments to pass to binaries (default: '--help')")
    parser.add_argument("-n", "--runs", type=int, default=50, help="Number of measured runs (default: 50)")
    parser.add_argument("-w", "--warmup", type=int, default=5, help="Number of warmup runs (default: 5)")
    parser.add_argument("--gui", action="store_true", help="Enable Wayland GUI lifecycle mode (tracks surface mapping & teardown)")
    parser.add_argument("--gui-settle", type=float, default=50.0, help="Settle time in ms for GUI before dismissal (default: 50ms)")
    parser.add_argument("--gui-timeout", type=float, default=5.0, help="Timeout in seconds for GUI mapping (default: 5.0s)")
    parser.add_argument("--allow-hybrid", action="store_true", help="Do not pin execution to Golden Cove P-cores")
    parser.add_argument("-s", "--skip-isa", action="store_true", help="Skip static objdump ISA disassembly audit")
    parser.add_argument("-o", "--json", type=Path, default=None, help="Save complete telemetry report to JSON")
    parser.add_argument("--diff-last", action="store_true", help="Compare single binary against the most recent historical run")
    parser.add_argument("--compare", type=str, default=None, help="Compare single binary against a specific historical Run ID")
    parser.add_argument("--history", type=int, nargs="?", const=10, default=None, help="Display recent historical runs and exit")
    parser.add_argument("--clear-history", action="store_true", help="Clear all stored benchmark history and exit")

    args = parser.parse_args()

    if args.clear_history:
        clear_history()
        return 0

    history = load_history()

    if args.history is not None:
        render_history_list(history, count=args.history)
        return 0

    primary = args.binary.resolve()
    if not primary.is_file() or not os.access(primary, os.X_OK):
        print(f"[ERR] Primary binary not found or not executable: {primary}", file=sys.stderr)
        return 1

    baseline = args.baseline.resolve() if args.baseline else None
    if baseline and (not baseline.is_file() or not os.access(baseline, os.X_OK)):
        print(f"[ERR] Baseline binary not found or not executable: {baseline}", file=sys.stderr)
        return 1

    parsed_args = [] if (args.args == "--help" and args.gui) else shlex.split(args.args)
    p_cores, e_cores = detect_hybrid_cpus()
    affinity = None if args.allow_hybrid or not p_cores else p_cores
    perf_events = detect_perf_events()

    render_banner(primary, baseline, parsed_args, args.runs, args.warmup, affinity, perf_events, args.gui)

    # 1. Static ISA Disassembly Audit
    n_isa, g_isa = {}, {}
    if not args.skip_isa:
        n_isa = analyze_binary_isa(primary)
        if baseline:
            g_isa = analyze_binary_isa(baseline)
            render_isa_comparison(n_isa, g_isa, primary.name, baseline.name)

    # 2. Benchmark Execution
    runner = (
        (lambda b: run_gui_sample(b, parsed_args, perf_events, args.gui_settle, args.gui_timeout))
        if args.gui
        else (lambda b: run_cli_sample(b, parsed_args, perf_events))
    )

    primary_samples: list[SampleMetrics] = []
    baseline_samples: list[SampleMetrics] = []

    with scoped_cpu_affinity(affinity):
        if baseline:
            # Dual Binary ABBA Interleaved Execution
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
            # Single Binary Standalone Execution
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
            "max_rss_kib": int(primary_stat_rss.mean),
        },
        "isa": n_isa,
        "samples": [dataclasses.asdict(x) for x in primary_samples],
    }

    if baseline:
        b_stat_wall = compute_stats([s.wall_ms for s in baseline_samples])
        b_stat_cyc = compute_stats([float(s.pcore_cycles) / 1e3 for s in baseline_samples])
        b_stat_rss = compute_stats([float(s.max_rss_kib) for s in baseline_samples])
        entry["baseline"] = {
            "name": baseline.name,
            "path": str(baseline),
            "metrics": {
                "wall_ms": round(b_stat_wall.mean, 2),
                "cycles_k": round(b_stat_cyc.mean, 1),
                "max_rss_kib": int(b_stat_rss.mean),
            },
            "isa": g_isa,
            "samples": [dataclasses.asdict(x) for x in baseline_samples],
        }

    save_run_record(entry)

    # 4. Rendering Output Tables
    if baseline:
        render_comparison_table(primary_samples, baseline_samples, primary.name, baseline.name, args.gui, perf_events)
        render_verdict_panel(primary_samples, baseline_samples, primary.name, baseline.name)
    else:
        render_single_table(primary_samples, primary.name, args.gui, perf_events)

        # Check for historical comparison request
        comp_entry: dict[str, Any] | None = None
        if args.compare:
            for item in history:
                if item.get("id") == args.compare:
                    comp_entry = item
                    break
            if not comp_entry:
                print(f"[WARN] Target historical Run ID '{args.compare}' not found.", file=sys.stderr)
        elif args.diff_last:
            matching = [x for x in history if x.get("binary_name") == primary.name and x.get("id") != run_id]
            if matching:
                comp_entry = matching[-1]
            elif history and history[-1].get("id") != run_id:
                comp_entry = history[-1]

        if comp_entry and "samples" in comp_entry:
            hist_samples = [SampleMetrics(**{k: v for k, v in d.items() if k in SampleMetrics.__annotations__}) for d in comp_entry["samples"]]
            label_hist = f"Historical ({comp_entry.get('id', '?')[:12]})"
            print()
            render_comparison_table(primary_samples, hist_samples, primary.name, label_hist, args.gui, perf_events)
            render_verdict_panel(primary_samples, hist_samples, primary.name, label_hist)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] Full benchmark report exported to {args.json}")

    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    try:
        sys.exit(main())
    except (KeyboardInterrupt, BrokenPipeError):
        sys.exit(130)
