#!/usr/bin/env python3
"""
ram_test.py - Ultimate DDR Memory Bandwidth & Latency Benchmark Suite
Target: Arch Linux | Kernel 7.4+ | Python 3.15+
"""

import argparse
from collections.abc import Iterator
import contextlib
from dataclasses import asdict, dataclass
import fcntl
import functools
import glob
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

lazy import csv
lazy import statistics

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

console = (
    Console(width=max(126, shutil.get_terminal_size((126, 24)).columns) if "COLUMNS" not in os.environ and shutil.get_terminal_size((126, 24)).columns == 80 and not sys.stdout.isatty() else None)
    if RICH_AVAILABLE
    else None
)


@dataclass(slots=True, kw_only=True)
class HardwareSpecs:
    cpu_model: str
    online_cpus: int
    numa_nodes: int
    optimal_p_core: str
    mem_type: str
    configured_speed_mts: int | None = None
    factory_speed_mts: int | None = None
    dimm_count: int | None = None
    channels: int | None = None
    bus_width_bits: int | None = None
    theoretical_max_gb_s: float | None = None
    total_ram_gib: float | None = None
    avail_ram_gib: float | None = None
    manufacturer: str | None = None
    part_number: str | None = None
    form_factor: str | None = None
    initial_dram_temps: list[tuple[str, float]] | None = None
    final_dram_temps: list[tuple[str, float]] | None = None


@dataclass(slots=True, kw_only=True)
class TestResult:
    name: str
    throughput_gb_s: float
    throughput_mib_s: float
    read_gb_s: float | None = None
    write_gb_s: float | None = None
    efficiency_pct: float | None = None
    latency_ns: float | None = None
    details: str = ""


@dataclass(slots=True, kw_only=True)
class CacheHierarchyResult:
    l1_kb: int
    l2_kb: int
    l3_kb: int
    dram_mb: int
    l1_ns: float
    l2_ns: float
    l3_ns: float | None
    dram_ns: float


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def tool_exists(name: str) -> bool:
    return shutil.which(name) is not None


def cache_sudo_privileges() -> bool:
    """Authenticate once; later privileged commands must never prompt."""
    if os.geteuid() == 0:
        return True
    if not tool_exists("sudo"):
        return False
    if subprocess.run(["sudo", "-n", "-v"], capture_output=True).returncode == 0:
        return True
    if not sys.stdin.isatty():
        return False
    return subprocess.run(["sudo", "-v"]).returncode == 0


def run_cmd(cmd: list[str], timeout: int = 60, *, isolate: bool = True) -> str:
    """Collect stable English output and terminate the whole job on cancellation."""
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "LC_ALL": "C", "LIBSMARTCOLS_JSON": "pretty"},
        start_new_session=isolate,
    ) as proc:
        try:
            stdout, _ = proc.communicate(timeout=timeout)
        except BaseException:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                if isolate:
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            proc.communicate()
            raise
        if proc.returncode:
            raise subprocess.CalledProcessError(proc.returncode, cmd, output=stdout)
        return stdout


def run_sudo_cmd(cmd: list[str], timeout: int = 60) -> str:
    return run_cmd(
        cmd if os.geteuid() == 0 else ["sudo", "-n", "--", *cmd],
        timeout,
        isolate=False,
    )


def get_online_cpu_count() -> int:
    return len(os.sched_getaffinity(0))


def _parse_cpu_range_list(text: str) -> list[int]:
    cpus: list[int] = []
    for part in text.strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            cpus.extend(range(int(start_s), int(end_s) + 1))
        else:
            cpus.append(int(part))
    return cpus


def probe_cpu_cache_sizes(target_core: str | None = None) -> tuple[int, int, int]:
    """Return observed data/unified cache sizes in KiB; zero means unavailable."""
    sizes = {"1": 0, "2": 0, "3": 0}
    core = target_core if target_core is not None else str(min(os.sched_getaffinity(0)))
    for index in Path(f"/sys/devices/system/cpu/cpu{core}/cache").glob("index*"):
        try:
            level = (index / "level").read_text(encoding="utf-8").strip()
            kind = (index / "type").read_text(encoding="utf-8").strip()
            size = (index / "size").read_text(encoding="utf-8").strip()
            if level in sizes and kind in ("Data", "Unified"):
                if match := re.fullmatch(r"(\d+)([KMGT])", size):
                    sizes[level] = int(match[1]) * (1024 ** "KMGT".index(match[2]))
        except (OSError, ValueError):
            continue
    return sizes["1"], sizes["2"], sizes["3"]


def get_optimal_p_core(allowed_cpus: set[int] | None = None) -> str:
    """Multi-tier heuristic to identify the highest-performing primary P-core / boost core.
    Ranks candidate CPUs by primary SMT thread status, CPU capacity, CPPC highest_perf,
    maximum hardware frequency, and L3 cache size."""
    candidates = allowed_cpus if allowed_cpus else os.sched_getaffinity(0)
    best_core = str(min(candidates))
    best_rank: tuple[int, float, float, float, int, int] = (-1, -1.0, -1.0, -1.0, -1, 0)

    for core_id in sorted(candidates):
        cpu_dir = Path(f"/sys/devices/system/cpu/cpu{core_id}")

        is_primary_thread = 1
        siblings_path = cpu_dir / "topology" / "thread_siblings_list"
        if siblings_path.exists():
            try:
                siblings = _parse_cpu_range_list(siblings_path.read_text(encoding="utf-8"))
                if siblings and core_id != min(siblings):
                    is_primary_thread = 0
            except (OSError, ValueError):
                pass

        capacity = 1024.0
        cap_path = cpu_dir / "cpu_capacity"
        if cap_path.exists():
            try:
                capacity = float(cap_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pass

        cppc_perf = 0.0
        cppc_path = cpu_dir / "acpi_cppc" / "highest_perf"
        if cppc_path.exists():
            try:
                cppc_perf = float(cppc_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pass

        max_freq = 0.0
        for freq_file in ("cpuinfo_max_freq", "scaling_max_freq"):
            freq_path = cpu_dir / "cpufreq" / freq_file
            if freq_path.exists():
                try:
                    max_freq = float(freq_path.read_text(encoding="utf-8").strip())
                    break
                except (OSError, ValueError):
                    pass

        _, _, l3_kib = probe_cpu_cache_sizes(str(core_id))
        rank = (is_primary_thread, capacity, cppc_perf, max_freq, l3_kib, -core_id)
        if rank > best_rank:
            best_rank = rank
            best_core = str(core_id)

    return best_core


def select_best_core_from_list(cores: str | None, fallback_core: str) -> str:
    if not cores:
        return fallback_core
    allowed = {int(c) for c in cores.split(",") if c.strip().isdigit()}
    return get_optimal_p_core(allowed) if allowed else fallback_core


@functools.cache
def get_executable_tmpdir() -> Path:
    """Locate an executable temporary directory without noexec restrictions."""
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    for base in (Path(tempfile.gettempdir()), cache_root / "ram_test_bench"):
        try:
            base.mkdir(parents=True, exist_ok=True)
            if os.statvfs(base).f_flag & os.ST_NOEXEC:
                continue
            with tempfile.TemporaryDirectory(dir=base) as directory:
                probe = Path(directory) / "probe"
                probe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                probe.chmod(0o755)
                run_cmd([str(probe)], timeout=5)
                return base
        except (OSError, subprocess.SubprocessError):
            continue
    raise RuntimeError("No executable temporary directory is available")


def probe_dram_temperatures() -> list[tuple[str, float]]:
    """Probe hardware thermal sensors for DDR4 (jc42) and DDR5 (spd5118) DRAM modules."""
    temps: list[tuple[str, float]] = []
    dimm_idx = 1

    for path in sorted(glob.glob("/sys/class/hwmon/hwmon*/temp*_input")):
        try:
            val_c = int(Path(path).read_text(encoding="utf-8").strip()) / 1000.0
            if not (0.0 < val_c < 125.0):
                continue
            name_path = Path(path).parent / "name"
            label_path = Path(path).with_name(Path(path).name.replace("_input", "_label"))
            name = name_path.read_text(encoding="utf-8").strip() if name_path.exists() else "hwmon"
            label = label_path.read_text(encoding="utf-8").strip() if label_path.exists() else Path(path).stem
            name_low = name.lower()
            label_low = label.lower()

            if any(k in name_low for k in ("spd5118", "jc42", "dram", "dimm")) or any(
                k in label_low for k in ("dimm", "dram", "memory")
            ):
                if name_low in ("spd5118", "jc42"):
                    sensor_name = f"DIMM {dimm_idx} ({name_low})"
                    dimm_idx += 1
                else:
                    sensor_name = f"{name} {label}"
                temps.append((sensor_name, val_c))
        except (OSError, ValueError):
            continue
    return temps


def format_temp_summary(
    initial: list[tuple[str, float]] | None,
    final: list[tuple[str, float]] | None = None,
) -> str:
    if not initial:
        return "No Sensor Data"
    final_map = dict(final) if final else {}
    parts: list[str] = []
    for label, init_c in initial:
        if label in final_map:
            fin_c = final_map[label]
            delta = fin_c - init_c
            parts.append(f"{label}: {init_c:.1f}°C -> {fin_c:.1f}°C ({delta:+.1f}°C)")
        else:
            parts.append(f"{label}: {init_c:.1f}°C")
    return " | ".join(parts)


def get_numa_node_count() -> int:
    for sysfs_list in ("/sys/devices/system/node/has_memory", "/sys/devices/system/node/online"):
        path = Path(sysfs_list)
        if path.exists():
            try:
                nodes = _parse_cpu_range_list(path.read_text(encoding="utf-8"))
                if nodes:
                    return len(nodes)
            except (OSError, ValueError):
                pass
    return max(1, len(list(Path("/sys/devices/system/node").glob("node[0-9]*"))))


MBW_CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ram_test_mbw" / "mbw"


def _get_mbw_binary() -> str | None:
    if executable := shutil.which("mbw"):
        return executable
    if MBW_CACHE.is_file() and os.access(MBW_CACHE, os.X_OK):
        return str(MBW_CACHE)
    return None


def check_dependencies(bench: str) -> None:
    """Verify required benchmark utilities and C toolchain availability."""
    required = {"taskset"}
    if bench in ("read", "write", "all"):
        required.add("sysbench")
    if bench in ("copy", "all"):
        required.add("stress-ng")
    missing = sorted(tool for tool in required if not tool_exists(tool))
    need_compiler = bench in ("cache", "latency", "all") or (
        bench in ("single", "all") and not _get_mbw_binary()
    )
    if need_compiler and not (tool_exists("gcc") or tool_exists("clang")):
        missing.append("gcc or clang")
    if missing:
        raise RuntimeError("Missing benchmark dependencies: " + ", ".join(missing))


def detect_hardware_specs(skip_sudo: bool = False) -> HardwareSpecs:
    cpu_model = "Unknown Processor"
    if tool_exists("lscpu"):
        try:
            out = run_cmd(["lscpu", "--json", "--hierarchic=never"])
            for entry in json.loads(out).get("lscpu", []):
                if entry.get("field") == "Model name:":
                    cpu_model = entry.get("data", cpu_model)
                    break
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    if cpu_model == "Unknown Processor":
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
            if m := re.search(r"model name\s+:\s+(.+)", cpuinfo):
                cpu_model = m.group(1).strip()
        except (OSError, ValueError):
            pass

    total_ram_gib, avail_ram_gib = None, None
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        if t_match := re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo):
            total_ram_gib = float(t_match.group(1)) / (1024.0 * 1024.0)
        if a_match := re.search(r"MemAvailable:\s+(\d+)\s+kB", meminfo):
            avail_ram_gib = float(a_match.group(1)) / (1024.0 * 1024.0)
    except (OSError, ValueError):
        pass

    mem_type, configured_speed_mts, factory_speed_mts = "RAM", None, None
    dimm_count, channels, bus_width_bits, max_gb_s = None, None, None, None
    manufacturer, part_number, form_factor = None, None, None

    if tool_exists("dmidecode") and not skip_sudo:
        try:
            dmi_out = run_sudo_cmd(["dmidecode", "-t", "memory"], timeout=10)
            devices: list[str] = []
            for device in dmi_out.split("Memory Device")[1:]:
                if not re.search(r"^\s*Size:\s+[1-9]\d*\s+[KMGT]i?B$", device, re.MULTILINE):
                    continue
                type_match = re.search(r"^\s*Type:\s+([^\n]+)", device, re.MULTILINE)
                dev_type = type_match.group(1).strip().upper() if type_match else ""
                if dev_type in ("FLASH", "ROM", "NON-VOLATILE", "NVRAM"):
                    continue
                devices.append(device)

            dimm_count = len(devices) or None
            installed = "\n".join(devices)

            def extract_first(field: str) -> str | None:
                for value in re.findall(rf"^\s*{field}:\s+([^\n]+)", installed, re.MULTILINE):
                    cleaned = value.strip()
                    if cleaned not in ("Unknown", "Not Specified", "None", "NO DIMM"):
                        return cleaned
                return None

            mem_type = extract_first("Type") or "RAM"
            manufacturer = extract_first("Manufacturer")
            part_number = extract_first("Part Number")
            form_factor = extract_first("Form Factor")
            cfg = [
                int(x)
                for x in re.findall(
                    r"^\s*Configured (?:Memory |Clock )?Speed:\s+(\d+)\s+(?:MT/s|MHz)",
                    installed,
                    re.MULTILINE,
                )
                if int(x) > 0
            ]
            rated = [
                int(x)
                for x in re.findall(
                    r"^\s*Speed:\s+(\d+)\s+(?:MT/s|MHz)",
                    installed,
                    re.MULTILINE,
                )
                if int(x) > 0
            ]
            configured_speed_mts = min(cfg) if cfg else None
            factory_speed_mts = min(rated) if rated else None
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    return HardwareSpecs(
        cpu_model=cpu_model,
        online_cpus=get_online_cpu_count(),
        numa_nodes=get_numa_node_count(),
        optimal_p_core=get_optimal_p_core(),
        mem_type=mem_type,
        configured_speed_mts=configured_speed_mts,
        factory_speed_mts=factory_speed_mts,
        dimm_count=dimm_count,
        channels=channels,
        bus_width_bits=bus_width_bits,
        theoretical_max_gb_s=max_gb_s,
        total_ram_gib=total_ram_gib,
        avail_ram_gib=avail_ram_gib,
        manufacturer=manufacturer,
        part_number=part_number,
        form_factor=form_factor,
        initial_dram_temps=probe_dram_temperatures(),
    )


GOVERNOR_WATCHDOG_HELPER = r"""
import contextlib, json, os, signal, sys
from pathlib import Path

with contextlib.suppress(OSError):
    os.setsid()
signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)

payload = json.loads(sys.argv[1])
targets = payload["targets"]
restore = payload["restore"]

def write_policies(mapping):
    failed = []
    # Write scaling_governor before energy_performance_preference
    ordered = sorted(mapping.items(), key=lambda kv: 1 if kv[0].endswith("energy_performance_preference") else 0)
    for name, value in ordered:
        try:
            p = Path(name)
            p.write_text(value, encoding="utf-8")
            if p.read_text(encoding="utf-8").strip() != value:
                failed.append(name)
        except OSError:
            failed.append(name)
    return failed

try:
    failed_init = write_policies(targets)
    sys.stdout.write(json.dumps({"failed": failed_init}) + "\n")
    sys.stdout.flush()
    # Block until parent closes stdin (normal exit or unexpected crash/kill)
    sys.stdin.read()
finally:
    write_policies(restore)
"""


@contextlib.contextmanager
def set_cpu_performance() -> Iterator[bool]:
    """Maintain a persistent privileged watchdog that sets performance policy controls
    and guarantees restoration on exit, SIGINT, SIGTERM, or sudo timeout."""
    state: dict[str, str] = {}
    targets: dict[str, str] = {}
    for policy in Path("/sys/devices/system/cpu/cpufreq").glob("policy*"):
        for control, available in (
            ("scaling_governor", "scaling_available_governors"),
            ("energy_performance_preference", "energy_performance_available_preferences"),
        ):
            path = policy / control
            avail_path = policy / available
            try:
                if not avail_path.exists() or not path.exists():
                    continue
                if "performance" not in avail_path.read_text(encoding="utf-8").split():
                    continue
                state[str(path)] = path.read_text(encoding="utf-8").strip()
                targets[str(path)] = "performance"
            except OSError:
                continue

    if not targets:
        yield False
        return

    payload = json.dumps({"targets": targets, "restore": state})
    cmd = [sys.executable, "-c", GOVERNOR_WATCHDOG_HELPER, payload]
    if os.geteuid() != 0:
        cmd = ["sudo", "-n", "--", *cmd]

    proc: subprocess.Popen[str] | None = None
    applied = False
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            start_new_session=False,
        )
        if proc.stdout is not None:
            line = proc.stdout.readline()
            if line:
                resp = json.loads(line)
                failed = resp.get("failed", [])
                if failed:
                    eprint("[Warning] CPU policy writes failed: " + ", ".join(failed))
                applied = not failed
            else:
                stderr_out = proc.stderr.read().strip() if proc.stderr else ""
                if stderr_out:
                    eprint(f"[Warning] CPU policy helper failed: {stderr_out}")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        eprint(f"[Warning] CPU policy update failed: {exc}")

    try:
        yield applied
    finally:
        if proc is not None:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=10)


LATENCY_SOURCE = r"""
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>
#include <sys/mman.h>

#ifndef MADV_HUGEPAGE
#define MADV_HUGEPAGE 14
#endif
#ifndef MADV_NOHUGEPAGE
#define MADV_NOHUGEPAGE 15
#endif
#ifndef MADV_COLLAPSE
#define MADV_COLLAPSE 25
#endif

static uint64_t state = UINT64_C(0x180ec6d33cfd0aba);
static uint64_t random64(void) {
    state ^= state >> 12;
    state ^= state << 25;
    state ^= state >> 27;
    return state * UINT64_C(2685821657736338717);
}

static size_t bounded(size_t range) {
    uint64_t threshold = (0ULL - (uint64_t)range) % range;
    uint64_t value;
    do { value = random64(); } while (value < threshold);
    return (size_t)(value % range);
}

#define STEP1(p)  p = *(void * volatile *)(p);
#define STEP4(p)  STEP1(p) STEP1(p) STEP1(p) STEP1(p)
#define STEP16(p) STEP4(p) STEP4(p) STEP4(p) STEP4(p)
#define STEP64(p) STEP16(p) STEP16(p) STEP16(p) STEP16(p)

int main(int argc, char **argv) {
    if (argc != 5) return 1;
    size_t bytes = strtoull(argv[1], NULL, 10);
    size_t stride = strtoull(argv[2], NULL, 10);
    int hugepages = atoi(argv[3]);
    int samples = atoi(argv[4]);
    if (stride < sizeof(void *) || stride % sizeof(void *) || samples < 1) return 1;
    size_t count = bytes / stride;
    if (count < 2 || count > SIZE_MAX / sizeof(size_t)) return 1;

    const size_t hp_align = 2ULL * 1024ULL * 1024ULL;
    size_t alloc_bytes = ((bytes + hp_align - 1) & ~(hp_align - 1)) + hp_align;
    char *raw = mmap(NULL, alloc_bytes, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (raw == MAP_FAILED) { perror("mmap"); return 1; }

    uintptr_t raw_addr = (uintptr_t)raw;
    uintptr_t aligned_addr = (raw_addr + (hp_align - 1)) & ~(uintptr_t)(hp_align - 1);
    char *buffer = (char *)aligned_addr;
    size_t advise_bytes = hugepages ? ((bytes + hp_align - 1) & ~(hp_align - 1)) : bytes;

    if (madvise(buffer, advise_bytes, hugepages ? MADV_HUGEPAGE : MADV_NOHUGEPAGE)) {
        perror("madvise"); munmap(raw, alloc_bytes); return 1;
    }

    size_t *order = malloc(count * sizeof(*order));
    if (!order) { perror("malloc"); munmap(raw, alloc_bytes); return 1; }
    for (size_t i = 0; i < count; ++i) order[i] = i;
    // Fisher-Yates shuffle over the visit-order array linked as a single Hamiltonian cycle
    for (size_t i = count - 1; i > 0; --i) {
        size_t j = bounded(i + 1);
        size_t tmp = order[i]; order[i] = order[j]; order[j] = tmp;
    }
    for (size_t i = 0; i < count; ++i) {
        *(void **)(buffer + order[i] * stride) = buffer + order[(i + 1) % count] * stride;
    }
    free(order);

    if (hugepages) {
        (void)madvise(buffer, advise_bytes, MADV_COLLAPSE);
    }

    void *cursor = buffer;
    size_t warmup = count > 4000000 ? count : 4000000;
    for (size_t i = 0; i < warmup; ++i) {
        STEP1(cursor);
    }

    size_t min_jumps = count > 1000000 ? count : 1000000;
    size_t loops = (min_jumps + 63) / 64;
    size_t total_jumps = loops * 64;

    for (int sample = 0; sample < samples; ++sample) {
        struct timespec start, end;
        if (clock_gettime(CLOCK_MONOTONIC_RAW, &start)) { munmap(raw, alloc_bytes); return 1; }
        for (size_t i = 0; i < loops; ++i) {
            STEP64(cursor);
        }
        __asm__ volatile("" : "+r"(cursor) :: "memory");
        if (clock_gettime(CLOCK_MONOTONIC_RAW, &end)) { munmap(raw, alloc_bytes); return 1; }
        double ns = (double)(end.tv_sec - start.tv_sec) * 1e9 + (double)(end.tv_nsec - start.tv_nsec);
        printf("%.4f\n", ns / (double)total_jumps);
    }
    munmap(raw, alloc_bytes);
    return 0;
}
"""

MBW_FALLBACK_SOURCE = r"""
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <sys/mman.h>

int main(int argc, char **argv) {
    // Compatible with: mbw -t 0 -n <runs> <size_mib>
    int runs = 10;
    size_t size_mib = 512;
    if (argc >= 6) {
        runs = atoi(argv[4]);
        size_mib = strtoull(argv[5], NULL, 10);
    } else if (argc >= 2) {
        size_mib = strtoull(argv[argc - 1], NULL, 10);
    }
    if (runs < 1 || size_mib < 1) return 1;
    size_t bytes = size_mib * 1024ULL * 1024ULL;
    char *src = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (src == MAP_FAILED) { perror("mmap"); return 1; }
    char *dst = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (dst == MAP_FAILED) { perror("mmap"); munmap(src, bytes); return 1; }
    memset(src, 0xA5, bytes);
    memset(dst, 0x5A, bytes);

    double total_sec = 0.0;
    for (int r = 0; r < runs; ++r) {
        struct timespec start, end;
        clock_gettime(CLOCK_MONOTONIC_RAW, &start);
        memcpy(dst, src, bytes);
        __asm__ volatile("" : : "r"(dst) : "memory");
        clock_gettime(CLOCK_MONOTONIC_RAW, &end);
        src[r % bytes] ^= dst[(r * 4096) % bytes];
        double sec = (double)(end.tv_sec - start.tv_sec) + (double)(end.tv_nsec - start.tv_nsec) * 1e-9;
        if (sec <= 0.0) sec = 1e-9;
        total_sec += sec;
    }
    double avg_mib_s = ((double)size_mib * (double)runs) / total_sec;
    printf("AVG\tMethod: MEMCPY\tElapsed: %.5f\tMiB: %.5f\tCopy: %.3f MiB/s\n",
           total_sec / runs, (double)size_mib, avg_mib_s);
    munmap(src, bytes);
    munmap(dst, bytes);
    return 0;
}
"""


@contextlib.contextmanager
def latency_binary() -> Iterator[str]:
    compiler = shutil.which("gcc") or shutil.which("clang")
    if not compiler:
        raise RuntimeError("Latency tests require gcc or clang")
    with tempfile.TemporaryDirectory(dir=get_executable_tmpdir()) as directory:
        source = Path(directory) / "latency.c"
        binary = Path(directory) / "latency"
        source.write_text(LATENCY_SOURCE, encoding="utf-8")
        run_cmd([compiler, "-std=c23", "-O3", "-Wall", "-Wextra", str(source), "-o", str(binary)])
        yield str(binary)


@contextlib.contextmanager
def single_core_copy_binary() -> Iterator[tuple[str, bool]]:
    """Yield (executable_path, is_native_fallback). Uses system/cached mbw if present,
    otherwise compiles the built-in C23 memcpy benchmark."""
    if mbw_bin := _get_mbw_binary():
        yield mbw_bin, False
        return
    compiler = shutil.which("gcc") or shutil.which("clang")
    if not compiler:
        raise RuntimeError("Single-core copy requires mbw, gcc, or clang")
    with tempfile.TemporaryDirectory(dir=get_executable_tmpdir()) as directory:
        source = Path(directory) / "mbw_fallback.c"
        binary = Path(directory) / "mbw_fallback"
        source.write_text(MBW_FALLBACK_SOURCE, encoding="utf-8")
        run_cmd([compiler, "-std=c23", "-O3", "-Wall", "-Wextra", str(source), "-o", str(binary)])
        yield str(binary), True


def measure_latency(binary: str, size_kib: int, core: str, hugepages: bool, samples: int) -> float:
    stride = 0
    for cache in Path(f"/sys/devices/system/cpu/cpu{core}/cache").glob("index*"):
        try:
            stride = max(stride, int((cache / "coherency_line_size").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    stride = stride or 64
    output = run_cmd(
        [
            "taskset",
            "--cpu-list",
            core,
            binary,
            str(size_kib * 1024),
            str(stride),
            str(int(hugepages)),
            str(samples),
        ],
        timeout=60 + samples * 15,
    )
    values = [float(value) for value in output.split()]
    if len(values) != samples or any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("Invalid latency sample output")
    return float(statistics.median(values))


def dram_working_set_mib(core: str) -> int:
    _, l2, l3 = probe_cpu_cache_sizes(core)
    return max(128, (max(l2, l3) * 4 + 1023) // 1024)


def run_cache_hierarchy_latency_test(
    cores: str | None = None,
    hugepages: bool = False,
    samples: int = 3,
    binary: str | None = None,
) -> CacheHierarchyResult | None:
    core = select_best_core_from_list(cores, get_optimal_p_core())
    l1, l2, l3 = probe_cpu_cache_sizes(core)
    if not (l1 > 0 and l2 > l1):
        eprint("[Warning] Cache hierarchy requires observable L1 and L2 cache sizes")
        return None

    l1_target = max(4, l1 // 2)
    l2_target = l2 // 2 if (l2 // 2) > l1 else (l1 + l2) // 2
    if not (0 < l1_target < l1 < l2_target < l2):
        eprint("[Warning] L1/L2 cache sizes cannot be cleanly isolated")
        return None

    l3_target: int | None = None
    if l3 > l2:
        candidate = l3 // 2 if (l3 // 2) > l2 else (l2 + l3) // 2
        if l2 < candidate < l3:
            l3_target = candidate

    dram_mib = dram_working_set_mib(core)
    try:
        with (contextlib.nullcontext(binary) if binary else latency_binary()) as executable:
            l1_ns = measure_latency(executable, l1_target, core, hugepages, samples)
            l2_ns = measure_latency(executable, l2_target, core, hugepages, samples)
            l3_ns = (
                measure_latency(executable, l3_target, core, hugepages, samples)
                if l3_target is not None
                else None
            )
            dram_ns = measure_latency(executable, dram_mib * 1024, core, hugepages, samples)
        return CacheHierarchyResult(
            l1_kb=l1_target,
            l2_kb=l2_target,
            l3_kb=l3_target or 0,
            dram_mb=dram_mib,
            l1_ns=l1_ns,
            l2_ns=l2_ns,
            l3_ns=l3_ns,
            dram_ns=dram_ns,
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        eprint(f"[Warning] Cache latency failed: {exc}")
        return None


def run_latency_test(
    array_size_mb: int,
    specs: HardwareSpecs,
    cores: str | None = None,
    hugepages: bool = False,
    samples: int = 3,
    binary: str | None = None,
) -> TestResult:
    core = select_best_core_from_list(cores, specs.optimal_p_core)
    size_mib = max(array_size_mb, dram_working_set_mib(core))
    latency = None
    details = (
        f"{size_mib} MiB, Fisher-Yates single-cycle pointer chase on CPU {core}; "
        f"median of {samples}; {'2 MiB THP + MADV_COLLAPSE' if hugepages else '4 KiB base pages'}"
    )
    try:
        with (contextlib.nullcontext(binary) if binary else latency_binary()) as executable:
            latency = measure_latency(executable, size_mib * 1024, core, hugepages, samples)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        eprint(f"[Warning] Random latency failed: {exc}")
        details = f"Failed: {exc}"
    return TestResult(
        name="Random Memory Latency",
        throughput_gb_s=0.0,
        throughput_mib_s=0.0,
        latency_ns=latency,
        details=details,
    )


def rate_bytes(value: str, unit: str) -> float:
    unit = unit.upper()
    prefix = unit[0] if unit[0] in "KMGT" else ""
    exponent = " KMGT".index(prefix) if prefix else 0
    return float(value) * ((1024 if "I" in unit else 1000) ** exponent)


def run_memory_test(
    operation: str,
    workers: int,
    run_time: int,
    specs: HardwareSpecs,
    cores: str | None,
) -> TestResult:
    name = f"Pure {operation.title()} (Multi-Thread)"
    core = select_best_core_from_list(cores, specs.optimal_p_core)
    _, l2, l3 = probe_cpu_cache_sizes(core)
    minimum = max(64 * 1024, max(l2, l3) * 2)
    block_kib = 1 << (minimum - 1).bit_length()
    cmd = [
        "sysbench",
        "memory",
        f"--threads={workers}",
        f"--time={run_time}",
        f"--memory-block-size={block_kib}K",
        "--memory-total-size=0",
        "--memory-scope=local",
        "--memory-access-mode=seq",
        f"--memory-oper={operation}",
        "run",
    ]
    if cores:
        cmd = ["taskset", "--cpu-list", cores, *cmd]
    try:
        stdout = run_cmd(cmd, timeout=run_time + 30)
        match = re.search(r"\(([0-9]+(?:\.[0-9]+)?)\s+([KMGT]?i?B)/sec\)", stdout, re.IGNORECASE)
        if not match:
            raise ValueError("sysbench throughput not found")
        bytes_s = rate_bytes(match[1], match[2])
        if bytes_s <= 0:
            raise ValueError("sysbench reported zero throughput")
        gb_s = bytes_s / 1e9
        rfo_note = " (write-allocate; excludes RFO read traffic)" if operation == "write" else ""
        return TestResult(
            name=name,
            throughput_gb_s=gb_s,
            throughput_mib_s=bytes_s / (2**20),
            read_gb_s=gb_s if operation == "read" else 0.0,
            write_gb_s=gb_s if operation == "write" else 0.0,
            efficiency_pct=(
                (gb_s / specs.theoretical_max_gb_s) * 100.0 if specs.theoretical_max_gb_s else None
            ),
            details=f"sysbench {block_kib // 1024} MiB local blocks, {workers} workers{rfo_note}",
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        eprint(f"[Warning] {name}: {exc}")
        return TestResult(name=name, throughput_gb_s=0.0, throughput_mib_s=0.0, details=f"Failed: {exc}")


def run_pure_read_test(
    workers: int, run_time: int, specs: HardwareSpecs, cores: str | None = None
) -> TestResult:
    return run_memory_test("read", workers, run_time, specs, cores)


def run_pure_write_test(
    workers: int, run_time: int, specs: HardwareSpecs, cores: str | None = None
) -> TestResult:
    return run_memory_test("write", workers, run_time, specs, cores)


def run_copy_stream_test(
    workers: int, run_time: int, specs: HardwareSpecs, cores: str | None = None
) -> TestResult:
    core = select_best_core_from_list(cores, specs.optimal_p_core)
    _, l2, l3 = probe_cpu_cache_sizes(core)
    llc_bytes = max(l2, l3) * 1024

    cmd: list[str] = []
    if cores:
        cmd.extend(["taskset", "--cpu-list", cores])

    actual_time = max(run_time, 10)
    cmd.extend(
        [
            "stress-ng",
            "--stream",
            str(workers),
            "--timeout",
            f"{actual_time}s",
            "--metrics-brief",
            "--verbose",
        ]
    )
    if llc_bytes > 0:
        cmd.extend(["--stream-l3-size", str(llc_bytes)])

    try:
        stdout = run_cmd(cmd, timeout=actual_time + 20)
    except (OSError, subprocess.SubprocessError) as exc:
        return TestResult(
            name="Stream Mix (Multi-Thread)",
            throughput_gb_s=0.0,
            throughput_mib_s=0.0,
            details=f"Failed ({type(exc).__name__}: stress-ng/taskset error)",
        )

    rate_re = re.compile(
        r"memory rate:\s+([0-9]+(?:\.[0-9]+)?)\s+([KMGT]?B)\s+read/sec,\s+([0-9]+(?:\.[0-9]+)?)\s+([KMGT]?B)\s+write/sec",
        re.IGNORECASE,
    )
    matches = rate_re.findall(stdout)

    if len(matches) != workers:
        eprint(f"[Warning] STREAM reported rates for {len(matches)} of {workers} workers")
        return TestResult(
            name="Stream Mix (Multi-Thread)",
            throughput_gb_s=0.0,
            throughput_mib_s=0.0,
            details="Failed: incomplete STREAM worker metrics",
        )

    read_bytes_s = sum(rate_bytes(value, unit.replace("B", "iB")) for value, unit, _, _ in matches)
    write_bytes_s = sum(rate_bytes(value, unit.replace("B", "iB")) for _, _, value, unit in matches)
    read_gb_s = read_bytes_s / 1e9
    write_gb_s = write_bytes_s / 1e9
    total_gb_s = read_gb_s + write_gb_s
    total_mib_s = (read_bytes_s + write_bytes_s) / (2**20)

    eff_pct = (
        (total_gb_s / specs.theoretical_max_gb_s) * 100.0
        if specs.theoretical_max_gb_s and specs.theoretical_max_gb_s > 0
        else None
    )

    return TestResult(
        name="Stream Mix (Multi-Thread)",
        throughput_gb_s=total_gb_s,
        throughput_mib_s=total_mib_s,
        read_gb_s=read_gb_s,
        write_gb_s=write_gb_s,
        efficiency_pct=eff_pct,
        latency_ns=None,
        details=f"STREAM copy/scale/add/triad, {workers} workers (Read: {read_gb_s:.1f} GB/s, Write: {write_gb_s:.1f} GB/s)",
    )


def run_single_core_test(
    size_mib: int,
    runs: int,
    run_time: int,
    specs: HardwareSpecs,
    cores: str | None = None,
    copy_target: tuple[str, bool] | None = None,
) -> TestResult:
    target_core = select_best_core_from_list(cores, specs.optimal_p_core)
    if specs.avail_ram_gib is not None:
        size_mib = max(64, min(size_mib, int(specs.avail_ram_gib * 1024 * 0.25)))

    try:
        ctx = (
            contextlib.nullcontext(copy_target)
            if copy_target is not None
            else single_core_copy_binary()
        )
        with ctx as (copy_bin, is_native):
            cmd = [
                "taskset",
                "--cpu-list",
                target_core,
                copy_bin,
                "-t",
                "0",
                "-n",
                str(runs),
                str(size_mib),
            ]
            stdout = run_cmd(cmd, timeout=max(run_time, 10) + 60)
            avg_re = re.compile(r"^AVG\s+Method:\s+(\S+).+?Copy:\s+([0-9.]+)\s+MiB/s", re.MULTILINE)
            averages = avg_re.findall(stdout)
            memcpy_mib_s = next((float(rate) for method, rate in averages if method == "MEMCPY"), 0.0)
            if not math.isfinite(memcpy_mib_s) or memcpy_mib_s <= 0:
                raise ValueError("MEMCPY average missing or invalid")
            gb_s = (2.0 * memcpy_mib_s * 1024.0 * 1024.0) / 1e9
            eff_pct = (
                ((gb_s / specs.theoretical_max_gb_s) * 100.0) if specs.theoretical_max_gb_s else None
            )
            engine = "C23 memcpy" if is_native else "mbw memcpy"
            return TestResult(
                name="Single-Core Copy (1 Core)",
                throughput_gb_s=gb_s,
                throughput_mib_s=2.0 * memcpy_mib_s,
                read_gb_s=gb_s / 2.0,
                write_gb_s=gb_s / 2.0,
                efficiency_pct=eff_pct,
                latency_ns=None,
                details=f"{engine} {size_mib} MiB on CPU {target_core} (read + write traffic; excludes RFO allocation)",
            )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        eprint(f"[Warning] Single-core copy failed: {exc}")

    return TestResult(
        name="Single-Core Copy (1 Core)",
        throughput_gb_s=0.0,
        throughput_mib_s=0.0,
        details="Failed (mbw/compiler unavailable, invalid output, or execution error)",
    )


def build_gauge(pct: float | None, width: int = 12, mode: str = "bandwidth") -> str:
    """Build a sleek horizontal gauge with high-contrast filled and empty segments."""
    if pct is None:
        return "[dim]—[/dim]"
    clamped = max(0.0, min(100.0, pct))
    filled = int(round((clamped / 100.0) * width))
    if clamped > 0.0 and filled == 0:
        filled = 1
    empty = width - filled

    if mode == "latency":
        fill_color = (
            "bright_green"
            if clamped <= 10.0
            else (
                "bright_cyan"
                if clamped <= 35.0
                else ("bright_yellow" if clamped <= 70.0 else "bright_magenta")
            )
        )
    else:
        fill_color = (
            "bright_green"
            if clamped >= 75.0
            else ("bright_yellow" if clamped >= 45.0 else "bright_cyan")
        )

    return (
        f"[{fill_color}]"
        + "━" * filled
        + f"[/{fill_color}][dim bright_black]"
        + "─" * empty
        + f"[/dim bright_black] [bold white]{clamped:5.1f}%[/bold white]"
    )


def render_header(specs: HardwareSpecs, governor_active: bool = True) -> None:
    if (
        specs.configured_speed_mts
        and specs.factory_speed_mts
        and specs.factory_speed_mts > specs.configured_speed_mts
    ):
        speed_str = f"{specs.configured_speed_mts} MT/s (Factory Rated: {specs.factory_speed_mts} MT/s)"
    elif specs.configured_speed_mts:
        speed_str = f"{specs.configured_speed_mts} MT/s"
    else:
        speed_str = "Unknown MT/s"

    dimm_str = (
        f"{specs.dimm_count} Modules"
        + (
            f" ({specs.bus_width_bits}-bit bus width, {specs.channels} channels supplied)"
            if specs.bus_width_bits
            else " (channel topology unknown; pass --channels)"
        )
        if specs.dimm_count
        else (
            f"{specs.channels} Channels ({specs.bus_width_bits}-bit bus width)"
            if specs.bus_width_bits
            else "Unknown Topology"
        )
    )
    max_str = (
        f"{specs.theoretical_max_gb_s:.2f} GB/s (user-supplied topology estimate)"
        if specs.theoretical_max_gb_s
        else "N/A (supply --channels for peak bus calculation)"
    )
    ram_cap_str = (
        f"{specs.total_ram_gib:.1f} GiB Installed ({specs.avail_ram_gib:.1f} GiB Available)"
        if specs.total_ram_gib and specs.avail_ram_gib
        else "System RAM"
    )
    mfg_str = specs.manufacturer or "Generic DRAM"
    form_str = specs.form_factor or "System Memory"
    gov_str = (
        "[bold green]Performance Mode[/bold green] (Policy controls verified & watchdog active)"
        if governor_active
        else "[dim]Standard Governor[/dim]"
    )
    numa_str = (
        f"[bold cyan]{specs.numa_nodes} NUMA Node[/bold cyan] (Uniform Memory Architecture)"
        if specs.numa_nodes == 1
        else f"[bold red]{specs.numa_nodes} NUMA Nodes[/bold red] (Non-Uniform Memory Architecture)"
    )

    temp_str = format_temp_summary(specs.initial_dram_temps, specs.final_dram_temps)
    if not RICH_AVAILABLE:
        print("=== RAM BANDWIDTH BENCHMARK SUITE ===")
        print(
            f"CPU: {specs.cpu_model} ({specs.online_cpus} online cores | Optimal Core: {specs.optimal_p_core})"
        )
        print(f"RAM: {specs.mem_type} @ {speed_str} | {ram_cap_str}")
        print(f"Topology: {dimm_str} | {mfg_str} {form_str}")
        print(f"NUMA: {specs.numa_nodes} Nodes | Temps: {temp_str}")
        print(f"Theoretical Max Bandwidth: {max_str}")
        print("=" * 60)
        return

    temp_rich = (
        f"[bold yellow]{temp_str}[/bold yellow]"
        if specs.initial_dram_temps
        else "[dim]No Sensor Data[/dim]"
    )

    table = Table(
        box=box.ROUNDED,
        show_header=False,
        border_style="bright_cyan",
        expand=True,
    )
    table.add_column("Property", style="bold bright_cyan", min_width=26, no_wrap=True)
    table.add_column("System Specifications & Architecture", style="bold bright_white", min_width=40, ratio=1)

    table.add_row("Processor Model", f"[bold white]{specs.cpu_model}[/bold white]")
    table.add_row(
        "Logical CPU Cores",
        f"[bold bright_green]{specs.online_cpus}[/bold bright_green] online (Optimal P-Core: CPU {specs.optimal_p_core})",
    )
    table.add_row("NUMA Architecture", numa_str)
    table.add_row("CPU Scaling & Frequency", gov_str)
    table.add_row("Installed Memory Capacity", f"[bold bright_magenta]{ram_cap_str}[/bold bright_magenta]")
    table.add_row(
        "Memory Technology & Speed",
        f"[bold yellow]{specs.mem_type}[/bold yellow] @ [bold bright_yellow]{speed_str}[/bold bright_yellow]",
    )
    table.add_row("Channel & Slot Topology", f"{dimm_str} ({mfg_str} {form_str})")
    table.add_row("Memory Thermal Sensors", temp_rich)
    table.add_row("Theoretical Peak Bandwidth", f"[bold bright_green]{max_str}[/bold bright_green]")

    console.print("\n[bold bright_cyan]▸ SYSTEM HARDWARE & MEMORY ARCHITECTURE[/bold bright_cyan]")
    console.print(table)


def render_cache_hierarchy_table(result: CacheHierarchyResult | None) -> None:
    if not result:
        return

    l1_size_str = f"{result.l1_kb} KiB"
    l2_size_str = f"{result.l2_kb} KiB" if result.l2_kb < 1024 else f"{result.l2_kb / 1024:.1f} MiB"
    l3_size_str = f"{result.l3_kb / 1024:.1f} MiB" if result.l3_kb > 0 else "N/A"
    dram_size_str = f"{result.dram_mb} MiB"

    if not RICH_AVAILABLE:
        print("\n=== CPU CACHE & MEMORY LATENCY HIERARCHY ===")
        print(f"L1 Data Cache    ({l1_size_str:7s}) : {result.l1_ns:.2f} ns")
        print(f"L2 Dedicated     ({l2_size_str:7s}) : {result.l2_ns:.2f} ns")
        if result.l3_ns is not None:
            print(f"L3 Shared LLC    ({l3_size_str:7s}) : {result.l3_ns:.2f} ns")
        print(f"Main System DRAM ({dram_size_str:7s}) : {result.dram_ns:.2f} ns")
        return

    table = Table(
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        border_style="bright_cyan",
        expand=True,
    )
    table.add_column("Memory Subsystem Level", style="bold bright_white", min_width=22, no_wrap=True)
    table.add_column("Buffer Size", justify="right", style="bold yellow", min_width=11, no_wrap=True)
    table.add_column("Access Delay", justify="right", style="bold bright_cyan", min_width=12, no_wrap=True)
    table.add_column("Relative Delay", justify="center", min_width=18, no_wrap=True)
    table.add_column("Microarchitectural Target & Speedup", style="bright_white", min_width=34, ratio=1)

    l1_rel = (result.l1_ns / result.dram_ns) * 100.0 if result.dram_ns > 0 else 0.0
    l2_rel = (result.l2_ns / result.dram_ns) * 100.0 if result.dram_ns > 0 else 0.0
    l1_speedup = f"{result.dram_ns / result.l1_ns:.1f}x faster" if result.l1_ns > 0 else "N/A"
    l2_speedup = f"{result.dram_ns / result.l2_ns:.1f}x faster" if result.l2_ns > 0 else "N/A"

    l1_gauge = build_gauge(l1_rel, width=10, mode="latency")
    l2_gauge = build_gauge(l2_rel, width=10, mode="latency")
    dram_gauge = build_gauge(100.0, width=10, mode="latency")

    table.add_row(
        "L1 Data Cache",
        l1_size_str,
        f"[bold bright_green]{result.l1_ns:.2f} ns[/bold bright_green]",
        l1_gauge,
        f"On-die L1 core data cache ([bold bright_green]{l1_speedup}[/bold bright_green] than DRAM)",
    )
    table.add_row(
        "L2 Dedicated Cache",
        l2_size_str,
        f"[bold bright_green]{result.l2_ns:.2f} ns[/bold bright_green]",
        l2_gauge,
        f"Per-core dedicated L2 cache ([bold bright_green]{l2_speedup}[/bold bright_green] than DRAM)",
    )
    if result.l3_ns is not None and result.l3_kb > 0:
        l3_rel = (result.l3_ns / result.dram_ns) * 100.0 if result.dram_ns > 0 else 0.0
        l3_speedup = f"{result.dram_ns / result.l3_ns:.1f}x faster" if result.l3_ns > 0 else "N/A"
        l3_gauge = build_gauge(l3_rel, width=10, mode="latency")
        table.add_row(
            "L3 Shared Smart Cache",
            l3_size_str,
            f"[bold bright_yellow]{result.l3_ns:.2f} ns[/bold bright_yellow]",
            l3_gauge,
            f"Shared LLC Smart Cache ([bold bright_yellow]{l3_speedup}[/bold bright_yellow] than DRAM)",
        )
    table.add_row(
        "Main System DRAM",
        dram_size_str,
        f"[bold bright_cyan]{result.dram_ns:.2f} ns[/bold bright_cyan]",
        dram_gauge,
        "Random pointer-chasing baseline; includes dTLB effects",
    )

    console.print("\n[bold bright_cyan]▸ CPU CACHE & MAIN MEMORY LATENCY HIERARCHY[/bold bright_cyan]")
    console.print(table)


def render_results_table(results: list[TestResult], specs: HardwareSpecs) -> None:
    if not RICH_AVAILABLE:
        print("\n=== BENCHMARK RESULTS SUMMARY ===")
        for r in results:
            eff = f"{r.efficiency_pct:5.1f}%" if r.efficiency_pct is not None else "—"
            tp = (
                f"{r.throughput_gb_s:7.2f} GB/s ({r.throughput_mib_s:9.1f} MiB/s)"
                if r.throughput_gb_s > 0
                else "—"
            )
            lat = f"{r.latency_ns:6.2f} ns" if r.latency_ns is not None else "—"
            print(f"{r.name:28s}: {tp} | {eff} of Max | Latency: {lat} | {r.details}")
        if specs.initial_dram_temps and specs.final_dram_temps:
            print(f"Thermal Profile: {format_temp_summary(specs.initial_dram_temps, specs.final_dram_temps)}")
        return

    has_peak = specs.theoretical_max_gb_s is not None and specs.theoretical_max_gb_s > 0
    table = Table(
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        border_style="bright_cyan",
        expand=True,
    )
    table.add_column("Benchmark Test Mode", style="bold bright_white", min_width=25, no_wrap=True)
    table.add_column("Measured Result", justify="right", min_width=15, no_wrap=True)
    if has_peak:
        table.add_column("Bus Efficiency", justify="center", min_width=18, no_wrap=True)
    else:
        table.add_column("Binary Rate / Mode", justify="right", style="bright_yellow", min_width=18, no_wrap=True)
    table.add_column("Test Configuration & Details", style="bright_white", min_width=36, ratio=1)

    for r in results:
        if r.name == "Random Memory Latency":
            res_str = (
                f"[bold bright_cyan]{r.latency_ns:.2f} ns[/bold bright_cyan]"
                if r.latency_ns
                else "[dim]Failed[/dim]"
            )
            secondary_str = (
                "[dim]Pointer Chase[/dim]"
                if has_peak
                else ("2 MiB THP" if "THP" in r.details else "4 KiB Pages")
            )
        else:
            res_str = (
                f"[bold bright_green]{r.throughput_gb_s:.2f} GB/s[/bold bright_green]"
                if r.throughput_gb_s > 0
                else "[dim]Failed[/dim]"
            )
            secondary_str = (
                build_gauge(r.efficiency_pct, width=10, mode="bandwidth")
                if has_peak
                else (f"{r.throughput_mib_s:,.0f} MiB/s" if r.throughput_mib_s > 0 else "[dim]—[/dim]")
            )

        table.add_row(
            r.name,
            res_str,
            secondary_str,
            Text(r.details),
        )

    console.print("\n[bold bright_cyan]▸ RAM BANDWIDTH & LATENCY BENCHMARK SUMMARY[/bold bright_cyan]")
    console.print(table)

    note_text = Text()
    note_text.append("Microarchitectural Performance Insights:\n", style="bold bright_yellow")
    note_text.append(" • ", style="bold bright_cyan")
    if specs.theoretical_max_gb_s:
        note_text.append("Theoretical Max Peak for your memory bus is ", style="bright_white")
        note_text.append(f"{specs.theoretical_max_gb_s:.2f} GB/s.\n", style="bold bright_green")
    else:
        note_text.append(
            "Pass --channels (e.g. --channels 2 for standard dual-channel DDR4/DDR5) to enable bus efficiency gauges.\n",
            style="bright_white",
        )

    if (
        specs.configured_speed_mts
        and specs.factory_speed_mts
        and specs.factory_speed_mts > specs.configured_speed_mts
    ):
        note_text.append(" • ", style="bold bright_cyan")
        note_text.append("Frequency Downclocking Detected: ", style="bold bright_white")
        note_text.append(
            f"Installed RAM is factory-rated for {specs.factory_speed_mts} MT/s but currently operating at {specs.configured_speed_mts} MT/s (check EXPO/XMP or CPU IMC limits).\n",
            style="bright_white",
        )

    if specs.initial_dram_temps and specs.final_dram_temps:
        note_text.append(" • ", style="bold bright_cyan")
        note_text.append("DRAM Thermal Excursion: ", style="bold bright_white")
        note_text.append(
            f"{format_temp_summary(specs.initial_dram_temps, specs.final_dram_temps)}\n",
            style="bold yellow",
        )

    single_core = next((r for r in results if r.name == "Single-Core Copy (1 Core)"), None)
    latency_res = next((r for r in results if r.name == "Random Memory Latency"), None)

    note_text.append(" • ", style="bold bright_cyan")
    note_text.append("Single-Core Throughput Limit: ", style="bold bright_white")
    if single_core and single_core.throughput_gb_s > 0:
        note_text.append(
            f"Measured single-core copy throughput is {single_core.throughput_gb_s:.1f} GB/s — bounded by per-core LFB/Miss Address Buffer concurrency.\n",
            style="bright_white",
        )
    else:
        note_text.append(
            f"Copy throughput on CPU {specs.optimal_p_core} depends on cache residency, the copy implementation, and per-core memory parallelism.\n",
            style="bright_white",
        )
    note_text.append(" • ", style="bold bright_cyan")
    note_text.append("Pure Read / Write Scaling: ", style="bold bright_white")
    peak_str = (
        f"{specs.theoretical_max_gb_s:.1f} GB/s"
        if specs.theoretical_max_gb_s
        else "the memory bus peak"
    )
    note_text.append(
        f"To approach {peak_str}, memory requests must be issued in parallel across multiple CPU cores ({specs.online_cpus} active).\n",
        style="bright_white",
    )
    note_text.append(" • ", style="bold bright_cyan")
    note_text.append("Random Access Latency vs Bandwidth: ", style="bold bright_white")
    if latency_res and latency_res.latency_ns:
        note_text.append(
            f"Random pointer-chasing latency is {latency_res.latency_ns:.1f} ns (includes cache, dTLB, and DRAM row-cycle effects); ",
            style="bright_white",
        )
    else:
        note_text.append(
            "Random latency uses a Fisher-Yates single-cycle pointer chase sized beyond the observed last-level cache; ",
            style="bright_white",
        )
    note_text.append(
        "streaming bandwidth achieves far lower effective per-line cost via hardware prefetching and bank parallelism.",
        style="bright_white",
    )

    panel = Panel(
        note_text,
        title="[bold bright_cyan]Understanding Single-Thread vs Multi-Thread RAM Bandwidth & Latency[/bold bright_cyan]",
        border_style="bright_cyan",
    )
    console.print(panel)


HISTORY_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "dusky"
    / "settings"
    / "ram_test"
)
HISTORY_FILE = HISTORY_DIR / "history.json"


@contextlib.contextmanager
def atomic_text(path: Path, *, newline: str | None = None) -> Iterator[Any]:
    """Publish a complete file atomically while respecting existing mode or user umask."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode: int | None = None
    if path.exists():
        with contextlib.suppress(OSError):
            existing_mode = path.stat().st_mode & 0o777

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline=newline,
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        try:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
            if existing_mode is not None:
                os.fchmod(stream.fileno(), existing_mode)
            else:
                current_umask = os.umask(0)
                os.umask(current_umask)
                os.fchmod(stream.fileno(), 0o666 & ~current_umask)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, data: object) -> None:
    with atomic_text(path) as stream:
        json.dump(data, stream, indent=2, allow_nan=False)


@contextlib.contextmanager
def history_lock() -> Iterator[None]:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with (HISTORY_DIR / ".lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def save_run_to_history(
    specs: HardwareSpecs,
    cache_hierarchy: CacheHierarchyResult | None,
    results: list[TestResult],
    args: argparse.Namespace,
) -> None:
    now_ns = time.time_ns()
    now_struct = time.localtime(now_ns / 1e9)
    now_ts = time.strftime("%Y-%m-%d %H:%M:%S", now_struct)
    time_short = time.strftime("%H:%M:%S", now_struct)
    date_short = time.strftime("%m-%d", now_struct)
    run_id = f"run_{now_ns}"
    metrics_map: dict[str, float] = {}
    for r in results:
        if r.name == "Random Memory Latency" and r.latency_ns:
            metrics_map["Random Memory Latency (ns)"] = r.latency_ns
        elif r.name != "Random Memory Latency" and r.throughput_gb_s > 0:
            metrics_map[r.name] = r.throughput_gb_s

    entry = {
        "id": run_id,
        "metrics_version": 2,
        "timestamp": now_ts,
        "time_short": time_short,
        "date_short": date_short,
        "bench": getattr(args, "bench", "all"),
        "hugepages": bool(getattr(args, "hugepages", False)),
        "workers": getattr(args, "workers", None) or specs.online_cpus,
        "cores": getattr(args, "cores", None),
        "time_sec": getattr(args, "time", 10),
        "optimal_core": specs.optimal_p_core,
        "cpu_model": specs.cpu_model,
        "mem_type": specs.mem_type,
        "configured_speed_mts": specs.configured_speed_mts,
        "cache": (
            {
                "l1_ns": cache_hierarchy.l1_ns,
                "l2_ns": cache_hierarchy.l2_ns,
                "l3_ns": cache_hierarchy.l3_ns,
                "dram_ns": cache_hierarchy.dram_ns,
            }
            if cache_hierarchy
            else None
        ),
        "metrics": metrics_map,
        "results_raw": [asdict(r) for r in results],
        "initial_temps": specs.initial_dram_temps,
        "final_temps": specs.final_dram_temps,
    }

    try:
        with history_lock():
            history = load_history()
            history.append(entry)
            retained = history[-100:]
            atomic_json(HISTORY_DIR / f"{run_id}.json", entry)
            atomic_json(HISTORY_FILE, retained)
            retained_ids = {
                item.get("id")
                for item in retained
                if isinstance(item.get("id"), str) and re.fullmatch(r"run_[0-9_]+", item["id"])
            }
            for run_file in HISTORY_DIR.glob("run_*.json"):
                if run_file.stem not in retained_ids:
                    run_file.unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        eprint(f"[Warning] Failed to save history: {exc}")


def load_history() -> list[dict[str, Any]]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(data, list) or any(not isinstance(entry, dict) for entry in data):
        raise ValueError(f"Invalid benchmark history: {HISTORY_FILE}")
    for entry in data:
        metrics = entry.get("metrics", {})
        cache = entry.get("cache") or {}
        if not isinstance(metrics, dict) or not isinstance(cache, dict):
            raise ValueError("Invalid historical metric mapping")
        for value in (*metrics.values(), *cache.values()):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("Invalid historical numeric metric")
        for name in ("id", "timestamp", "time_short"):
            if name in entry and not isinstance(entry[name], str):
                raise ValueError(f"Invalid historical {name}")
    return data


def clear_history() -> None:
    with history_lock():
        for path in HISTORY_DIR.glob("run_*.json"):
            path.unlink(missing_ok=True)
        HISTORY_FILE.unlink(missing_ok=True)
    print(f"Cleared benchmark history in {HISTORY_DIR}")


def render_history_comparison(history: list[dict[str, Any]], count: int = 7) -> None:
    """Render a side-by-side comparison matrix of recent benchmark runs with best/worst scores highlighted."""
    if not history:
        msg = "No previous benchmark history found in ~/.config/dusky/settings/ram_test/"
        if RICH_AVAILABLE:
            console.print(f"[bold yellow]󰘓 {msg}[/bold yellow]")
        else:
            print(msg)
        return

    comparable = [run for run in history if run.get("metrics_version") == 2]
    if not comparable:
        print("No comparable runs using the current measurement method yet.")
        return

    runs = comparable[-count:]

    def _round_disp(val: float | None) -> float | None:
        if val is None:
            return None
        return round(val, 2) if abs(val) < 10.0 else round(val, 1)

    def _fmt_delta_plain(curr: float | None, ref: float | None) -> str:
        if curr is None or ref is None or ref == 0:
            return "—"
        pct = ((curr - ref) / ref) * 100.0
        return "0.0%" if abs(pct) < 0.05 else f"{pct:+.1f}%"

    def _fmt_delta_rich(curr: float | None, ref: float | None, metric_mode: str) -> str:
        if curr is None or ref is None or ref == 0:
            return "[dim]—[/dim]"
        pct = ((curr - ref) / ref) * 100.0
        if abs(pct) < 0.05:
            return "[dim white]0.0% —[/dim white]"
        is_good = (pct < 0) if metric_mode == "latency" else (pct > 0)
        color = "bold bright_green" if is_good else "bold bright_red"
        arrow = "▼" if pct < 0 else "▲"
        return f"[{color}]{pct:+.1f}% {arrow}[/{color}]"

    metrics_meta = [
        (
            "Random DRAM Latency (ns)",
            "latency",
            "bold bright_cyan",
            lambda r: r.get("metrics", {}).get("Random Memory Latency (ns)")
            or (r.get("cache") or {}).get("dram_ns"),
        ),
        ("L1 Data Cache (ns)", "latency", "bold cyan", lambda r: (r.get("cache") or {}).get("l1_ns")),
        ("L2 Dedicated Cache (ns)", "latency", "bold cyan", lambda r: (r.get("cache") or {}).get("l2_ns")),
        ("L3 Smart Cache (ns)", "latency", "bold cyan", lambda r: (r.get("cache") or {}).get("l3_ns")),
        (None, None, None, None),
        (
            "Single-Core Copy (GB/s)",
            "bandwidth",
            "bold bright_yellow",
            lambda r: r.get("metrics", {}).get("Single-Core Copy (1 Core)"),
        ),
        (
            "Pure Read Multi (GB/s)",
            "bandwidth",
            "bold bright_green",
            lambda r: r.get("metrics", {}).get("Pure Read (Multi-Thread)"),
        ),
        (
            "Pure Write Multi (GB/s)",
            "bandwidth",
            "bold bright_green",
            lambda r: r.get("metrics", {}).get("Pure Write (Multi-Thread)"),
        ),
        (
            "STREAM Mix Multi (GB/s)",
            "bandwidth",
            "bold bright_green",
            lambda r: r.get("metrics", {}).get("Stream Mix (Multi-Thread)"),
        ),
    ]

    if not RICH_AVAILABLE:
        print(f"\n=== MULTI-RUN BENCHMARK COMPARISON (Last {len(runs)} Runs) ===")
        header_cols = [f"{'Metric':<24s}"]
        for i, r in enumerate(runs):
            t_short = r.get("time_short", (r.get("timestamp", "").split() or ["?"])[-1])
            header_cols.append(f"R{i+1} ({t_short:>8s})")
        header_cols.extend([" Min–Max   ", " Δ Prev ", " Δ Base "])
        header = " | ".join(header_cols)
        print(header)
        print("-" * len(header))
        for item in metrics_meta:
            if item[0] is None:
                print("-" * len(header))
                continue
            label, mode, label_style, extractor = item
            vals = [_round_disp(extractor(r)) for r in runs]
            valid_vals = [v for v in vals if v is not None]
            if not valid_vals:
                continue
            row_items = [f"{label:<24s}"]
            for v in vals:
                if v is not None:
                    row_items.append(f"{v:>12.2f}" if v < 10.0 else f"{v:>12.1f}")
                else:
                    row_items.append(f"{'—':^12s}")
            min_v, max_v = min(valid_vals), max(valid_vals)
            min_max = f"{min_v:.2f}–{max_v:.2f}" if max_v < 10.0 else f"{min_v:.1f}–{max_v:.1f}"
            latest_v = vals[-1]
            prev_v = valid_vals[-2] if latest_v is not None and len(valid_vals) >= 2 else None
            base_v = valid_vals[0] if latest_v is not None and len(valid_vals) >= 2 else None
            row_items.append(f"{min_max:^11s}")
            row_items.append(f"{_fmt_delta_plain(latest_v, prev_v):>8s}")
            row_items.append(f"{_fmt_delta_plain(latest_v, base_v):>8s}")
            print(" | ".join(row_items))
        return

    t = Table(
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        border_style="bright_cyan",
        show_header=True,
        expand=True,
        padding=(0, 1),
    )
    t.add_column("Benchmark Metric", min_width=24, ratio=1, no_wrap=True)

    for i, r in enumerate(runs):
        is_latest = i == len(runs) - 1
        tag = "[bold bright_green]Latest[/bold bright_green]" if is_latest else f"R{i+1}"
        hp = "*" if r.get("hugepages") else ""
        raw_t = r.get("time_short", (r.get("timestamp", "").split() or [""])[-1])
        t_short = ":".join(raw_t.split(":")[:2])
        t.add_column(f"{tag}{hp}\n[white]{t_short}[/white]", justify="right", min_width=6, no_wrap=True)

    t.add_column("Min–Max", justify="center", style="bold bright_white", min_width=10, no_wrap=True)
    t.add_column("Δ Prev", justify="right", min_width=8, no_wrap=True)
    t.add_column("Δ Base", justify="right", min_width=8, no_wrap=True)

    for item in metrics_meta:
        if item[0] is None:
            t.add_section()
            continue
        label, mode, label_style, extractor = item
        vals = [_round_disp(extractor(r)) for r in runs]
        valid_vals = [v for v in vals if v is not None]
        if not valid_vals:
            continue
        min_v, max_v = min(valid_vals), max(valid_vals)
        best_v = min_v if mode == "latency" else max_v
        worst_v = max_v if mode == "latency" else min_v

        row = [f"[{label_style}]{label}[/{label_style}]"]
        for v in vals:
            if v is not None:
                formatted = f"{v:.2f}" if v < 10.0 else f"{v:.1f}"
                if len(valid_vals) > 1 and v == best_v and best_v != worst_v:
                    cell_str = f"[bold bright_green]{formatted}[/bold bright_green]"
                elif len(valid_vals) > 1 and v == worst_v and best_v != worst_v:
                    cell_str = f"[bold bright_red]{formatted}[/bold bright_red]"
                else:
                    cell_str = f"[bright_white]{formatted}[/bright_white]"
                row.append(cell_str)
            else:
                row.append("[dim]—[/dim]")

        min_max = f"{min_v:.2f}–{max_v:.2f}" if max_v < 10.0 else f"{min_v:.1f}–{max_v:.1f}"
        latest_v = vals[-1]
        prev_v = valid_vals[-2] if latest_v is not None and len(valid_vals) >= 2 else None
        base_v = valid_vals[0] if latest_v is not None and len(valid_vals) >= 2 else None
        delta_prev_str = _fmt_delta_rich(latest_v, prev_v, mode)
        delta_base_str = _fmt_delta_rich(latest_v, base_v, mode)

        row.extend([min_max, delta_prev_str, delta_base_str])
        t.add_row(*row)

    console.print(
        f"\n[bold bright_cyan]▸ MULTI-RUN BENCHMARK COMPARISON (LAST {len(runs)} RUNS)[/bold bright_cyan]"
    )
    console.print(t)


def export_report(
    filename: str,
    specs: HardwareSpecs,
    cache_hierarchy: CacheHierarchyResult | None,
    results: list[TestResult],
) -> None:
    export_path = Path(filename).expanduser().resolve()
    export_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "metrics_version": 2,
        "hardware_specs": asdict(specs),
        "cache_hierarchy_latency_ns": asdict(cache_hierarchy) if cache_hierarchy else None,
        "benchmark_results": [asdict(r) for r in results],
    }

    if export_path.suffix.lower() == ".json":
        atomic_json(export_path, data)
        msg = f"Exported benchmark report to JSON: {export_path}"
        if RICH_AVAILABLE:
            console.print(f"[bold green]󰄬 {msg}[/bold green]")
        else:
            print(msg)
    elif export_path.suffix.lower() == ".csv":
        speed_meta = (
            f"{specs.mem_type} @ {specs.configured_speed_mts} MT/s"
            if specs.configured_speed_mts
            else specs.mem_type
        )
        topo_meta = (
            f"{specs.channels} Channels, {specs.bus_width_bits}-bit Bus Width"
            if specs.channels and specs.bus_width_bits
            else (f"{specs.dimm_count} Modules (Channels Unspecified)" if specs.dimm_count else "Unknown")
        )
        with atomic_text(export_path, newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["# SYSTEM HARDWARE METADATA"])
            writer.writerow(["# CPU Model", specs.cpu_model])
            writer.writerow(["# Memory Speed", speed_meta])
            writer.writerow(["# Topology", topo_meta])
            writer.writerow(
                ["# Thermal Profile", format_temp_summary(specs.initial_dram_temps, specs.final_dram_temps)]
            )
            writer.writerow([])

            writer.writerow(
                [
                    "Metric / Test",
                    "Throughput (GB/s)",
                    "Throughput (MiB/s)",
                    "Efficiency (%)",
                    "Latency (ns)",
                    "Details",
                ]
            )
            if cache_hierarchy:
                writer.writerow(
                    ["L1 Data Cache Latency", "—", "—", "—", f"{cache_hierarchy.l1_ns:.2f}", f"{cache_hierarchy.l1_kb} KiB"]
                )
                writer.writerow(
                    ["L2 Dedicated Cache Latency", "—", "—", "—", f"{cache_hierarchy.l2_ns:.2f}", f"{cache_hierarchy.l2_kb} KiB"]
                )
                if cache_hierarchy.l3_ns is not None and cache_hierarchy.l3_kb > 0:
                    writer.writerow(
                        ["L3 Shared Cache Latency", "—", "—", "—", f"{cache_hierarchy.l3_ns:.2f}", f"{cache_hierarchy.l3_kb} KiB"]
                    )
                writer.writerow(
                    ["Main System DRAM Hierarchy Latency", "—", "—", "—", f"{cache_hierarchy.dram_ns:.2f}", f"{cache_hierarchy.dram_mb} MiB"]
                )
            for r in results:
                tp_gb = f"{r.throughput_gb_s:.2f}" if r.throughput_gb_s > 0 else "—"
                tp_mib = f"{r.throughput_mib_s:.1f}" if r.throughput_mib_s > 0 else "—"
                writer.writerow(
                    [
                        r.name,
                        tp_gb,
                        tp_mib,
                        f"{r.efficiency_pct:.1f}" if r.efficiency_pct is not None else "—",
                        f"{r.latency_ns:.2f}" if r.latency_ns else "—",
                        r.details,
                    ]
                )
        msg = f"Exported benchmark report to CSV: {export_path}"
        if RICH_AVAILABLE:
            console.print(f"[bold green]󰄬 {msg}[/bold green]")
        else:
            print(msg)
    else:
        raise ValueError(f"Unsupported export format: {export_path.suffix}")


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def normalize_cores(value: str) -> str:
    """Accept taskset's CPU-list ranges/strides and require allowed online CPUs."""
    allowed = os.sched_getaffinity(0)
    selected: set[int] = set()
    for part in value.split(","):
        match = re.fullmatch(r"(\d+)(?:-(\d+)(?::([1-9]\d*))?)?", part.strip())
        if not match:
            raise argparse.ArgumentTypeError("invalid CPU list (use 0,2-6 or 0-6:2)")
        start = int(match[1])
        end = int(match[2] or match[1])
        step = int(match[3] or 1)
        if start > end or end > max(allowed):
            raise argparse.ArgumentTypeError("CPU range is reversed or outside the available CPUs")
        selected.update(range(start, end + 1, step))
    if not selected <= allowed:
        raise argparse.ArgumentTypeError("CPU list includes CPUs outside this process's affinity")
    return ",".join(map(str, sorted(selected)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ultimate Hardware-Agnostic RAM Bandwidth & Latency Benchmark Suite"
    )
    parser.add_argument(
        "--bench",
        choices=["read", "write", "copy", "single", "latency", "cache", "all"],
        default="all",
        help="Benchmark mode to run non-interactively (default: all).",
    )
    parser.add_argument(
        "--workers",
        type=positive_int,
        help="Workers for multi-core tests (default: CPUs selected by --cores or process affinity).",
    )
    parser.add_argument(
        "--time",
        type=positive_int,
        default=10,
        help="Duration for read/write/STREAM (STREAM minimum: 10 seconds; default: 10).",
    )
    parser.add_argument(
        "--size",
        type=positive_int,
        default=1024,
        help="Array size in MiB for single-core copy test (default: 1024).",
    )
    parser.add_argument(
        "--cores",
        type=normalize_cores,
        help="Core range string to pin tests to (e.g. 0-13 or 0-7).",
    )
    parser.add_argument(
        "--hugepages",
        action="store_true",
        help="Request 2 MiB Transparent Huge Pages (MADV_HUGEPAGE + MADV_COLLAPSE) in latency tests.",
    )
    parser.add_argument(
        "--samples",
        type=positive_int,
        default=3,
        help="Number of latency samples per test; median reported (default: 3).",
    )
    parser.add_argument(
        "--export",
        help="Path to export benchmark results in JSON or CSV format (e.g. --export report.json).",
    )
    parser.add_argument(
        "--compare",
        "--history",
        dest="compare",
        nargs="?",
        const=7,
        type=positive_int,
        help="Compare the last N runs using the current measurement method (default: 7).",
    )
    parser.add_argument(
        "--clear-history",
        action="store_true",
        help="Clear all saved history state files in ~/.config/dusky/settings/ram_test/.",
    )
    parser.add_argument(
        "--no-governor",
        action="store_true",
        help="Skip optimizing CPU performance governor.",
    )
    parser.add_argument(
        "--channels",
        type=positive_int,
        help="Known active memory channels; enables theoretical bus peak calculation.",
    )
    parser.add_argument(
        "--channel-width",
        type=positive_int,
        default=64,
        help="Data bits per supplied channel (default: 64 per standard DDR4/DDR5 DIMM channel; use 32 only if counting individual DDR5 subchannels).",
    )
    args = parser.parse_args()
    if args.export and Path(args.export).suffix.lower() not in (".json", ".csv"):
        parser.error("--export requires a .json or .csv path")

    if args.clear_history:
        try:
            clear_history()
        except OSError as exc:
            eprint(f"Error: {exc}")
            return 1
        return 0

    if args.compare is not None and not any(
        arg == "--bench" or arg.startswith("--bench=") for arg in sys.argv[1:]
    ):
        try:
            render_history_comparison(load_history(), count=args.compare)
        except (OSError, ValueError) as exc:
            eprint(f"Error: {exc}")
            return 1
        return 0

    try:
        check_dependencies(args.bench)
        has_sudo = cache_sudo_privileges()

        specs = detect_hardware_specs(skip_sudo=not has_sudo)
        if args.cores:
            specs.optimal_p_core = select_best_core_from_list(args.cores, specs.optimal_p_core)

        if args.channels:
            specs.channels = args.channels
            specs.bus_width_bits = args.channels * args.channel_width
            if specs.configured_speed_mts:
                specs.theoretical_max_gb_s = specs.configured_speed_mts * specs.bus_width_bits / 8000.0

        pinned_count = len(args.cores.split(",")) if args.cores else specs.online_cpus
        workers = args.workers or pinned_count
        if args.workers and args.workers > pinned_count:
            eprint(
                f"[Warning] Requested --workers={args.workers} exceeds {pinned_count} pinned CPUs; thread oversubscription may occur."
            )
        args.workers = workers

        results: list[TestResult] = []
        cache_hierarchy: CacheHierarchyResult | None = None
        governor_ctx = (
            set_cpu_performance()
            if not args.no_governor and has_sudo
            else contextlib.nullcontext(False)
        )
        native_ctx = (
            latency_binary()
            if args.bench in ("cache", "latency", "all")
            else contextlib.nullcontext(None)
        )
        copy_ctx = (
            single_core_copy_binary()
            if args.bench in ("single", "all")
            else contextlib.nullcontext(None)
        )
        with governor_ctx as governor_active, native_ctx as binary, copy_ctx as copy_target:
            render_header(specs, governor_active=governor_active)
            progress_ctx = (
                Progress(
                    SpinnerColumn("dots", style="cyan"),
                    TextColumn("{task.description}"),
                    console=console,
                    transient=True,
                )
                if RICH_AVAILABLE
                else contextlib.nullcontext(None)
            )
            with progress_ctx as progress:
                for mode in ("cache", "latency", "single", "read", "write", "copy"):
                    if args.bench not in (mode, "all"):
                        continue
                    description = f"Running {mode} memory benchmark..."
                    task = progress.add_task(description, total=None) if progress else None
                    if not progress:
                        print(description)
                    try:
                        match mode:
                            case "cache":
                                cache_hierarchy = run_cache_hierarchy_latency_test(
                                    args.cores, args.hugepages, args.samples, binary
                                )
                            case "latency":
                                results.append(
                                    run_latency_test(
                                        128, specs, args.cores, args.hugepages, args.samples, binary
                                    )
                                )
                            case "single":
                                results.append(
                                    run_single_core_test(
                                        args.size, 10, args.time, specs, args.cores, copy_target
                                    )
                                )
                            case "read":
                                results.append(
                                    run_pure_read_test(workers, args.time, specs, args.cores)
                                )
                            case "write":
                                results.append(
                                    run_pure_write_test(workers, args.time, specs, args.cores)
                                )
                            case "copy":
                                results.append(
                                    run_copy_stream_test(workers, args.time, specs, args.cores)
                                )
                    finally:
                        if progress and task is not None:
                            progress.remove_task(task)

        specs.final_dram_temps = probe_dram_temperatures()
        if cache_hierarchy:
            render_cache_hierarchy_table(cache_hierarchy)
        if results:
            render_results_table(results, specs)
        save_run_to_history(specs, cache_hierarchy, results, args)
        history = load_history()
        if len(history) >= 2:
            render_history_comparison(history, count=args.compare or 7)
        if args.export:
            export_report(args.export, specs, cache_hierarchy, results)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        eprint(f"Error: {exc}")
        return 1
    except KeyboardInterrupt:
        if RICH_AVAILABLE:
            console.print("\n[bold yellow]󰞅 Benchmark interrupted by user.[/bold yellow]")
        else:
            print("\nBenchmark interrupted by user.")
        return 130

    had_failure = any(
        (r.latency_ns is None or r.latency_ns <= 0)
        if r.name == "Random Memory Latency"
        else r.throughput_gb_s <= 0
        for r in results
    )
    if args.bench in ("cache", "all") and cache_hierarchy is None:
        had_failure = True

    if had_failure:
        if RICH_AVAILABLE:
            console.print("[bold red]󰀨 One or more benchmarks failed — check warnings above.[/bold red]")
        else:
            print("One or more benchmarks failed — check warnings above.")
        return 1
    return 0


if __name__ == "__main__":
    def _handle_sigterm(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    sys.exit(main())
