#!/usr/bin/env python3
"""
ram_test.py - Ultimate DDR Memory Bandwidth & Latency Benchmark Suite
Target: Arch Linux | Kernel 7.3+ | Python 3.15+
"""

import argparse
import contextlib
import fcntl
lazy import csv
import glob
import json
import math
import os
import re
import shutil
import signal
lazy import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

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

console = Console() if RICH_AVAILABLE else None

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
    l3_ns: float
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
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "LC_ALL": "C", "LIBSMARTCOLS_JSON": "pretty"},
        start_new_session=isolate,
    ) as proc:
        try:
            stdout, _ = proc.communicate(timeout=timeout)
        except BaseException:
            # stress-ng has workers: killing just its parent leaves tests running.
            with contextlib.suppress(ProcessLookupError):
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
    # Keep the controlling terminal: sudo timestamps can be bound to its session.
    return run_cmd(cmd if os.geteuid() == 0 else ["sudo", "-n", "--", *cmd], timeout, isolate=False)


def get_online_cpu_count() -> int:
    return len(os.sched_getaffinity(0))


def get_optimal_p_core() -> str:
    """Comprehensive multi-tier heuristic to identify highest performance P-core / boost core.
    Ranks allowed CPUs using CPPC, maximum frequency, and capacity."""
    online_cpus = os.sched_getaffinity(0)
    best_core = str(min(online_cpus))
    highest_score = -1.0

    for core_id in sorted(online_cpus):
        score = 0.0
        cpu_dir = Path(f"/sys/devices/system/cpu/cpu{core_id}")

        # 1. Check CPPC highest_perf (favored boost core rating)
        cppc_path = cpu_dir / "acpi_cppc" / "highest_perf"
        if cppc_path.exists():
            try:
                score += float(cppc_path.read_text(encoding="utf-8").strip()) * 1000.0
            except (OSError, ValueError):
                pass

        # 2. Check cpuinfo_max_freq (maximum hardware frequency in kHz)
        freq_path = cpu_dir / "cpufreq" / "cpuinfo_max_freq"
        if not freq_path.exists():
            freq_path = cpu_dir / "cpufreq" / "scaling_max_freq"
        if freq_path.exists():
            try:
                score += float(freq_path.read_text(encoding="utf-8").strip()) / 1000.0
            except (OSError, ValueError):
                pass

        # 3. Check cpu_capacity
        cap_path = cpu_dir / "cpu_capacity"
        if cap_path.exists():
            try:
                score += float(cap_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pass

        if score > highest_score:
            highest_score = score
            best_core = str(core_id)

    return best_core


def get_executable_tmpdir() -> Path:
    """Probe executable storage without deleting another invocation's files."""
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    for base in (Path(tempfile.gettempdir()), cache_root / "ram_test_bench"):
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            probe = Path(directory) / "probe"
            probe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            probe.chmod(0o755)
            try:
                run_cmd([str(probe)], timeout=5)
                return base
            except (OSError, subprocess.SubprocessError):
                continue
    raise RuntimeError("No executable temporary directory is available")


def probe_dram_temperatures() -> list[tuple[str, float]]:
    """Probe hardware thermal sensors for DRAM modules with clean numbered labeling."""
    temps: list[tuple[str, float]] = []
    dimm_idx = 1

    for path in sorted(glob.glob("/sys/class/hwmon/hwmon*/temp*_input")):
        try:
            val_c = int(Path(path).read_text(encoding="utf-8").strip()) / 1000.0
            name_path = Path(path).parent / "name"
            label_path = Path(path).with_name(Path(path).name.replace("_input", "_label"))
            name = name_path.read_text(encoding="utf-8").strip() if name_path.exists() else "hwmon"
            label = label_path.read_text(encoding="utf-8").strip() if label_path.exists() else Path(path).stem

            if "spd5118" in name.lower() or "dram" in name.lower() or "dimm" in label.lower() or "memory" in label.lower():
                if "spd5118" in name.lower():
                    sensor_name = f"DIMM {dimm_idx} (spd5118)"
                    dimm_idx += 1
                else:
                    sensor_name = f"{name} {label}"
                temps.append((sensor_name, val_c))
        except (OSError, ValueError):
            continue
    return temps


def get_numa_node_count() -> int:
    return max(1, len(list(Path("/sys/devices/system/node").glob("node[0-9]*"))))


def probe_cpu_cache_sizes(target_core: str | None = None) -> tuple[int, int, int]:
    """Return observed data/unified cache sizes in KiB; zero means unavailable."""
    sizes = {"1": 0, "2": 0, "3": 0}
    core = target_core or get_optimal_p_core()
    for index in Path(f"/sys/devices/system/cpu/cpu{core}/cache").glob("index*"):
        try:
            level = (index / "level").read_text(encoding="utf-8").strip()
            kind = (index / "type").read_text(encoding="utf-8").strip()
            size = (index / "size").read_text(encoding="utf-8").strip()
            if level in sizes and kind in ("Data", "Unified"):
                if match := re.fullmatch(r"(\d+)([KMGT])", size):
                    sizes[level] = int(match[1]) * 1024 ** "KMGT".index(match[2])
        except (OSError, ValueError):
            continue
    return sizes["1"], sizes["2"], sizes["3"]


MBW_CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ram_test_mbw" / "mbw"


def _get_mbw_binary() -> str | None:
    if executable := shutil.which("mbw"):
        return executable
    if MBW_CACHE.is_file() and os.access(MBW_CACHE, os.X_OK):
        return str(MBW_CACHE)
    return None


def check_dependencies(bench: str) -> None:
    """Use preinstalled tools; benchmarking must also work offline."""
    required = {"taskset"}
    if bench in ("read", "write", "all"):
        required.add("sysbench")
    if bench in ("copy", "all"):
        required.add("stress-ng")
    missing = sorted(tool for tool in required if not tool_exists(tool))
    if bench in ("cache", "latency", "all") and not (tool_exists("gcc") or tool_exists("clang")):
        missing.append("gcc or clang")
    if bench in ("single", "all") and not _get_mbw_binary():
        missing.append("mbw (install beforehand or provide the cached binary)")
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
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    total_ram_gib, avail_ram_gib = None, None
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        if t_match := re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo):
            total_ram_gib = float(t_match.group(1)) / (1024.0 * 1024.0)
        if a_match := re.search(r"MemAvailable:\s+(\d+)\s+kB", meminfo):
            avail_ram_gib = float(a_match.group(1)) / (1024.0 * 1024.0)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    mem_type, configured_speed_mts, factory_speed_mts = "RAM", None, None
    dimm_count, channels, bus_width_bits, max_gb_s = None, None, None, None
    manufacturer, part_number, form_factor = None, None, None

    if tool_exists("dmidecode") and not skip_sudo:
        try:
            dmi_out = run_sudo_cmd(["dmidecode", "-t", "memory"], timeout=10)
            devices = [device for device in dmi_out.split("Memory Device")[1:]
                       if re.search(r"^\s*Size: [1-9]\d* [KMGT]i?B$", device, re.MULTILINE)]
            dimm_count = len(devices) or None
            installed = "\n".join(devices)

            def extract_first(field: str) -> str | None:
                for value in re.findall(rf"^\s*{field}: ([^\n]+)", installed, re.MULTILINE):
                    if value.strip() not in ("Unknown", "Not Specified", "None"):
                        return value.strip()
                return None

            mem_type = extract_first("Type") or "RAM"
            manufacturer = extract_first("Manufacturer")
            part_number = extract_first("Part Number")
            form_factor = extract_first("Form Factor")
            cfg = re.findall(r"^\s*Configured (?:Memory |Clock )?Speed: (\d+) (?:MT/s|MHz)", installed, re.MULTILINE)
            rated = re.findall(r"^\s*Speed: (\d+) (?:MT/s|MHz)", installed, re.MULTILINE)
            configured_speed_mts = min(map(int, cfg)) if cfg else None
            factory_speed_mts = min(map(int, rated)) if rated else None
            # SMBIOS slots/locators do not establish active channel topology.
            # Leave the bus limit unknown unless the user supplies channel data.
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


@contextlib.contextmanager
def set_cpu_performance():
    """Set supported policy controls and restore them, including on SIGTERM."""
    state: dict[str, str] = {}
    targets: dict[str, str] = {}
    for policy in Path("/sys/devices/system/cpu/cpufreq").glob("policy*"):
        for control, available in (
            ("scaling_governor", "scaling_available_governors"),
            ("energy_performance_preference", "energy_performance_available_preferences"),
        ):
            path = policy / control
            try:
                if "performance" not in (policy / available).read_text(encoding="utf-8").split():
                    continue
                value = path.read_text(encoding="utf-8").strip()
                state[str(path)] = value
                targets[str(path)] = "performance"
            except OSError:
                continue

    helper = """import json, sys
from pathlib import Path
failed = []
for name, value in json.loads(sys.argv[1]).items():
    try:
        path = Path(name)
        path.write_text(value, encoding="utf-8")
        if path.read_text(encoding="utf-8").strip() != value:
            failed.append(name)
    except OSError:
        failed.append(name)
print(json.dumps(failed))
"""

    def apply(values: dict[str, str]) -> bool:
        if not values:
            return True
        try:
            failed = json.loads(run_sudo_cmd([sys.executable, "-c", helper, json.dumps(values)], timeout=10))
            if failed:
                eprint("[Warning] CPU policy writes failed: " + ", ".join(failed))
            return not failed
        except (OSError, subprocess.SubprocessError) as exc:
            eprint(f"[Warning] CPU policy update failed: {exc}")
            return False

    previous = signal.getsignal(signal.SIGTERM)

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    try:
        applied = apply(targets)
        # Setting a governor can itself change EPP; apply it after governors.
        applied = apply({name: value for name, value in targets.items()
                         if name.endswith("energy_performance_preference")}) and applied
        yield applied and bool(targets)
    finally:
        try:
            apply(state)
            apply({name: value for name, value in state.items()
                   if name.endswith("energy_performance_preference")})
        finally:
            signal.signal(signal.SIGTERM, previous)


LATENCY_SOURCE = r"""
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>
#include <sys/mman.h>

static uint64_t state = UINT64_C(0x180ec6d33cfd0aba);
static uint64_t random64(void) {
    state ^= state >> 12;
    state ^= state << 25;
    state ^= state >> 27;
    return state * UINT64_C(2685821657736338717);
}

static size_t bounded(size_t range) {
    uint64_t threshold = -((uint64_t)range) % range;
    uint64_t value;
    do { value = random64(); } while (value < threshold);
    return value % range;
}

int main(int argc, char **argv) {
    if (argc != 5) return 1;
    size_t bytes = strtoull(argv[1], NULL, 10);
    size_t stride = strtoull(argv[2], NULL, 10);
    int hugepages = atoi(argv[3]);
    int samples = atoi(argv[4]);
    if (stride < sizeof(void *) || stride % sizeof(void *) || samples < 1) return 1;
    size_t count = bytes / stride;
    if (count < 2 || count > SIZE_MAX / sizeof(size_t)) return 1;
    char *buffer = mmap(NULL, bytes, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (buffer == MAP_FAILED) { perror("mmap"); return 1; }
    if (madvise(buffer, bytes, hugepages ? MADV_HUGEPAGE : MADV_NOHUGEPAGE)) {
        perror("madvise"); munmap(buffer, bytes); return 1;
    }
    size_t *order = malloc(count * sizeof(*order));
    if (!order) { perror("malloc"); munmap(buffer, bytes); return 1; }
    for (size_t i = 0; i < count; ++i) order[i] = i;
    for (size_t i = count - 1; i > 0; --i) {
        size_t j = bounded(i + 1);
        size_t tmp = order[i]; order[i] = order[j]; order[j] = tmp;
    }
    for (size_t i = 0; i < count; ++i)
        *(void **)(buffer + order[i] * stride) = buffer + order[(i + 1) % count] * stride;
    free(order);
    // Volatile pointer loads preserve both warmup and the timed dependency chain.
    void *cursor = buffer;
    for (size_t i = 0; i < count; ++i) cursor = *(void * volatile *)cursor;
    size_t jumps = count > 1000000 ? count : 1000000;
    for (int sample = 0; sample < samples; ++sample) {
        struct timespec start, end;
        if (clock_gettime(CLOCK_MONOTONIC_RAW, &start)) return 1;
        for (size_t i = 0; i < jumps; ++i) cursor = *(void * volatile *)cursor;
        if (clock_gettime(CLOCK_MONOTONIC_RAW, &end)) return 1;
        double ns = (double)(end.tv_sec - start.tv_sec) * 1e9 + end.tv_nsec - start.tv_nsec;
        printf("%.4f\n", ns / jumps);
    }
    munmap(buffer, bytes);
    return 0;
}
"""


@contextlib.contextmanager
def latency_binary():
    compiler = shutil.which("gcc") or shutil.which("clang")
    if not compiler:
        raise RuntimeError("Latency tests require gcc or clang")
    with tempfile.TemporaryDirectory(dir=get_executable_tmpdir()) as directory:
        source = Path(directory) / "latency.c"
        binary = Path(directory) / "latency"
        source.write_text(LATENCY_SOURCE, encoding="utf-8")
        run_cmd([compiler, "-std=c23", "-O3", "-Wall", "-Wextra", str(source), "-o", str(binary)])
        yield str(binary)


def measure_latency(binary: str, size_kib: int, core: str, hugepages: bool, samples: int) -> float:
    stride = 0
    for cache in Path(f"/sys/devices/system/cpu/cpu{core}/cache").glob("index*"):
        try:
            stride = max(stride, int((cache / "coherency_line_size").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    stride = stride or 64  # Fallback when sysfs does not expose a line size.
    output = run_cmd(["taskset", "--cpu-list", core, binary, str(size_kib * 1024),
                      str(stride), str(int(hugepages)), str(samples)], timeout=60 + samples * 10)
    values = [float(value) for value in output.split()]
    if len(values) != samples or any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("Invalid latency sample output")
    return statistics.median(values)


def dram_working_set_mib(core: str) -> int:
    _, l2, l3 = probe_cpu_cache_sizes(core)
    return max(128, (max(l2, l3) * 4 + 1023) // 1024)


def run_cache_hierarchy_latency_test(
    cores: str | None = None, hugepages: bool = False, samples: int = 3,
    binary: str | None = None,
) -> CacheHierarchyResult | None:
    core = cores.split(",")[0] if cores else get_optimal_p_core()
    l1, l2, l3 = probe_cpu_cache_sizes(core)
    if not (l1 and l2 and l3):
        eprint("[Warning] Cache hierarchy requires observed L1/L2/L3 sizes")
        return None
    sizes = (l1 // 2, l2 // 2, l3 // 2)
    if not (0 < sizes[0] < l1 < sizes[1] < l2 < sizes[2] < l3):
        eprint("[Warning] Cache sizes cannot isolate the requested hierarchy")
        return None
    dram_mib = dram_working_set_mib(core)
    try:
        with (contextlib.nullcontext(binary) if binary else latency_binary()) as executable:
            values = [measure_latency(executable, size, core, hugepages, samples)
                      for size in (*sizes, dram_mib * 1024)]
        return CacheHierarchyResult(l1_kb=sizes[0], l2_kb=sizes[1], l3_kb=sizes[2],
                                    dram_mb=dram_mib, l1_ns=values[0], l2_ns=values[1],
                                    l3_ns=values[2], dram_ns=values[3])
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        eprint(f"[Warning] Cache latency failed: {exc}")
        return None


def run_latency_test(
    array_size_mb: int, specs: HardwareSpecs, cores: str | None = None,
    hugepages: bool = False, samples: int = 3, binary: str | None = None,
) -> TestResult:
    core = cores.split(",")[0] if cores else specs.optimal_p_core
    size_mib = max(array_size_mb, dram_working_set_mib(core))
    latency = None
    details = f"{size_mib} MiB, randomized cache-line pointer cycle on CPU {core}; median of {samples}; "
    details += "THP requested" if hugepages else "base pages"
    try:
        with (contextlib.nullcontext(binary) if binary else latency_binary()) as executable:
            latency = measure_latency(executable, size_mib * 1024, core, hugepages, samples)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        eprint(f"[Warning] Random latency failed: {exc}")
        details = f"Failed: {exc}"
    return TestResult(name="Random Memory Latency", throughput_gb_s=0.0,
                      throughput_mib_s=0.0, latency_ns=latency, details=details)


def rate_bytes(value: str, unit: str) -> float:
    unit = unit.upper()
    prefix = unit[0] if unit[0] in "KMGT" else ""
    exponent = " KMGT".index(prefix) if prefix else 0
    return float(value) * (1024 if "I" in unit else 1000) ** exponent


def run_memory_test(
    operation: str, workers: int, run_time: int, specs: HardwareSpecs, cores: str | None,
) -> TestResult:
    name = f"Pure {operation.title()} (Multi-Thread)"
    core = cores.split(",")[0] if cores else specs.optimal_p_core
    _, l2, l3 = probe_cpu_cache_sizes(core)
    # sysbench requires a power-of-two block; keep each worker beyond the LLC.
    minimum = max(64 * 1024, max(l2, l3) * 2)
    block_kib = 1 << (minimum - 1).bit_length()
    cmd = ["sysbench", "memory", f"--threads={workers}", f"--time={run_time}",
           f"--memory-block-size={block_kib}K", "--memory-total-size=0",
           "--memory-scope=local", "--memory-access-mode=seq", f"--memory-oper={operation}", "run"]
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
        return TestResult(name=name, throughput_gb_s=gb_s, throughput_mib_s=bytes_s / 2**20,
                          read_gb_s=gb_s if operation == "read" else 0.0,
                          write_gb_s=gb_s if operation == "write" else 0.0,
                          efficiency_pct=gb_s / specs.theoretical_max_gb_s * 100 if specs.theoretical_max_gb_s else None,
                          details=f"sysbench {block_kib // 1024} MiB local blocks, {workers} workers")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        eprint(f"[Warning] {name}: {exc}")
        return TestResult(name=name, throughput_gb_s=0.0, throughput_mib_s=0.0, details=f"Failed: {exc}")


def run_pure_read_test(workers: int, run_time: int, specs: HardwareSpecs, cores: str | None = None) -> TestResult:
    return run_memory_test("read", workers, run_time, specs, cores)


def run_pure_write_test(workers: int, run_time: int, specs: HardwareSpecs, cores: str | None = None) -> TestResult:
    return run_memory_test("write", workers, run_time, specs, cores)


def run_copy_stream_test(
    workers: int, run_time: int, specs: HardwareSpecs, cores: str | None = None
) -> TestResult:
    cmd = []
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

    try:
        stdout = run_cmd(cmd, timeout=actual_time + 15)
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

    # stress-ng labels binary megabytes as MB (STRESS_MB == 1024 * 1024).
    if len(matches) != workers:
        eprint(f"[Warning] STREAM reported rates for {len(matches)} of {workers} workers")
        return TestResult(name="Stream Mix (Multi-Thread)", throughput_gb_s=0.0,
                          throughput_mib_s=0.0, details="Failed: incomplete STREAM worker metrics")
    read_bytes_s = sum(rate_bytes(value, unit.replace("B", "iB")) for value, unit, _, _ in matches)
    write_bytes_s = sum(rate_bytes(value, unit.replace("B", "iB")) for _, _, value, unit in matches)
    read_gb_s = read_bytes_s / 1e9
    write_gb_s = write_bytes_s / 1e9
    total_gb_s = read_gb_s + write_gb_s
    total_mib_s = (read_bytes_s + write_bytes_s) / 2**20

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
    size_mib: int, runs: int, run_time: int, specs: HardwareSpecs, cores: str | None = None
) -> TestResult:
    target_core = cores.split(",")[0] if cores else specs.optimal_p_core
    if specs.avail_ram_gib is not None:
        size_mib = max(1, min(size_mib, int(specs.avail_ram_gib * 1024 * 0.25)))

    mbw_bin = _get_mbw_binary()
    if mbw_bin:
        try:
            cmd = ["taskset", "--cpu-list", target_core, mbw_bin, "-t", "0", "-n", str(runs), str(size_mib)]
            stdout = run_cmd(cmd, timeout=run_time + 60)
            avg_re = re.compile(r"^AVG\s+Method:\s+(\S+).+?Copy:\s+([0-9.]+)\s+MiB/s", re.MULTILINE)
            averages = avg_re.findall(stdout)
            memcpy_mib_s = next((float(rate) for method, rate in averages if method == "MEMCPY"), 0.0)
            if not math.isfinite(memcpy_mib_s) or memcpy_mib_s <= 0:
                raise ValueError("mbw MEMCPY average missing or invalid")
            gb_s = (2 * memcpy_mib_s * 1024.0 * 1024.0) / 1e9
            eff_pct = ((gb_s / specs.theoretical_max_gb_s) * 100.0) if specs.theoretical_max_gb_s else None
            return TestResult(
                name="Single-Core Copy (1 Core)",
                throughput_gb_s=gb_s,
                throughput_mib_s=2 * memcpy_mib_s,
                read_gb_s=gb_s / 2.0,
                write_gb_s=gb_s / 2.0,
                efficiency_pct=eff_pct,
                latency_ns=None,
                details=f"mbw memcpy {size_mib} MiB on Core {target_core} (read + write traffic; excludes write allocation)",
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            eprint(f"[Warning] mbw failed: {exc}")

    return TestResult(
        name="Single-Core Copy (1 Core)",
        throughput_gb_s=0.0,
        throughput_mib_s=0.0,
        details="Failed (mbw unavailable, invalid output, or execution error)"
    )


def build_gauge(pct: float | None, width: int = 12, mode: str = "bandwidth") -> str:
    """Build a sleek, thin horizontal gauge with high-contrast filled and empty segments."""
    if pct is None:
        return "[dim]—[/dim]"
    clamped = max(0.0, min(100.0, pct))
    filled = int(round((clamped / 100.0) * width))
    if clamped > 0.0 and filled == 0:
        filled = 1
    empty = width - filled

    if mode == "latency":
        fill_color = "bright_green" if clamped <= 10.0 else ("bright_cyan" if clamped <= 35.0 else ("bright_yellow" if clamped <= 70.0 else "bright_magenta"))
    else:
        fill_color = "bright_green" if clamped >= 75.0 else ("bright_yellow" if clamped >= 45.0 else "bright_cyan")

    bar = f"[{fill_color}]" + "━" * filled + f"[/{fill_color}][dim bright_black]" + "─" * empty + f"[/dim bright_black] [bold white]{clamped:5.1f}%[/bold white]"
    return bar


def render_header(specs: HardwareSpecs, governor_active: bool = True):
    if specs.configured_speed_mts and specs.factory_speed_mts and specs.factory_speed_mts > specs.configured_speed_mts:
        speed_str = f"{specs.configured_speed_mts} MT/s (Factory Rated: {specs.factory_speed_mts} MT/s)"
    elif specs.configured_speed_mts:
        speed_str = f"{specs.configured_speed_mts} MT/s"
    else:
        speed_str = "Unknown MT/s"

    dimm_str = (
        f"{specs.dimm_count} Modules" + (f" ({specs.bus_width_bits}-bit width, user-supplied)" if specs.bus_width_bits else " (channel topology unknown)")
        if specs.dimm_count
        else "Unknown Topology"
    )
    max_str = (
        f"{specs.theoretical_max_gb_s:.2f} GB/s (user-supplied topology estimate)"
        if specs.theoretical_max_gb_s
        else "N/A"
    )
    ram_cap_str = (
        f"{specs.total_ram_gib:.1f} GiB Installed ({specs.avail_ram_gib:.1f} GiB Available)"
        if specs.total_ram_gib and specs.avail_ram_gib
        else "System RAM"
    )
    mfg_str = specs.manufacturer or "Generic DRAM"
    form_str = specs.form_factor or "System Memory"
    gov_str = (
        "[bold green]Performance Mode[/bold green] (Policy controls verified)"
        if governor_active
        else "[dim]Standard Governor[/dim]"
    )
    numa_str = (
        f"[bold cyan]{specs.numa_nodes} NUMA Nodes[/bold cyan] (Uniform Memory Architecture)"
        if specs.numa_nodes == 1
        else f"[bold red]{specs.numa_nodes} NUMA Nodes[/bold red] (Non-uniform memory architecture)"
    )

    temp_str = "No Sensor Data"
    if specs.initial_dram_temps:
        t_list = [f"{lbl}: {val:.1f}°C" for lbl, val in specs.initial_dram_temps]
        temp_str = " | ".join(t_list)
    if not RICH_AVAILABLE:
        print("=== RAM BANDWIDTH BENCHMARK SUITE ===")
        print(f"CPU: {specs.cpu_model} ({specs.online_cpus} online cores | Optimal Core: {specs.optimal_p_core})")
        print(f"RAM: {specs.mem_type} @ {speed_str} | {ram_cap_str}")
        print(f"Topology: {dimm_str} | {mfg_str} {form_str}")
        print(f"NUMA: {specs.numa_nodes} Nodes | Temps: {temp_str}")
        print(f"Theoretical Max Bandwidth: {max_str}")
        print("=" * 60)
        return

    temp_rich = f"[bold yellow]{temp_str}[/bold yellow]" if specs.initial_dram_temps else "[dim]No Sensor Data[/dim]"

    table = Table(
        box=box.ROUNDED,
        show_header=False,
        border_style="bright_cyan",
        expand=False,
    )
    table.add_column("Property", style="bold bright_cyan", min_width=27, no_wrap=True)
    table.add_column("System Specifications & Architecture", style="bold bright_white", min_width=62, no_wrap=True)

    table.add_row("Processor Model", f"[bold white]{specs.cpu_model}[/bold white]")
    table.add_row("Logical CPU Cores", f"[bold bright_green]{specs.online_cpus}[/bold bright_green] cores (Optimal Core: Core {specs.optimal_p_core})")
    table.add_row("NUMA Architecture", numa_str)
    table.add_row("CPU Scaling & Frequency", gov_str)
    table.add_row("Installed Memory Capacity", f"[bold bright_magenta]{ram_cap_str}[/bold bright_magenta]")
    table.add_row("Memory Technology & Speed", f"[bold yellow]{specs.mem_type}[/bold yellow] @ [bold bright_yellow]{speed_str}[/bold bright_yellow]")
    table.add_row("Channel & Slot Topology", f"{dimm_str} ({mfg_str} {form_str})")
    table.add_row("Memory Thermal Sensors", temp_rich)
    table.add_row("Theoretical Peak Bandwidth", f"[bold bright_green]{max_str}[/bold bright_green]")

    console.print("\n[bold bright_cyan] 󰍛 SYSTEM HARDWARE & MEMORY ARCHITECTURE[/bold bright_cyan]")
    console.print(table)


def render_cache_hierarchy_table(result: CacheHierarchyResult | None):
    if not result:
        return

    l1_size_str = f"{result.l1_kb} KiB"
    l2_size_str = f"{result.l2_kb} KiB" if result.l2_kb < 1024 else f"{result.l2_kb / 1024:.1f} MiB"
    l3_size_str = f"{result.l3_kb / 1024:.1f} MiB"
    dram_size_str = f"{result.dram_mb} MiB"

    if not RICH_AVAILABLE:
        print("\n=== CPU CACHE & MEMORY LATENCY HIERARCHY ===")
        print(f"L1 Data Cache    ({l1_size_str:7s}) : {result.l1_ns:.2f} ns")
        print(f"L2 Dedicated     ({l2_size_str:7s}) : {result.l2_ns:.2f} ns")
        print(f"L3 Shared LLC    ({l3_size_str:7s}) : {result.l3_ns:.2f} ns")
        print(f"Main System DRAM ({dram_size_str:7s}): {result.dram_ns:.2f} ns")
        return

    table = Table(
        title="[bold bright_cyan]󰔛 CPU Cache & Main Memory Latency Hierarchy[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        expand=True,
    )
    table.add_column("Memory Subsystem Level", style="bold bright_white", width=25)
    table.add_column("Buffer Size", justify="center", style="bold yellow", width=12)
    table.add_column("Access Delay (ns)", justify="right", style="bold bright_cyan", width=17)
    table.add_column("Relative Delay", justify="center", width=20)
    table.add_column("Microarchitectural Target & Speedup", style="bright_white")

    l1_rel = (result.l1_ns / result.dram_ns) * 100.0 if result.dram_ns > 0 else 0.0
    l2_rel = (result.l2_ns / result.dram_ns) * 100.0 if result.dram_ns > 0 else 0.0
    l3_rel = (result.l3_ns / result.dram_ns) * 100.0 if result.dram_ns > 0 else 0.0

    l1_speedup = f"{result.dram_ns / result.l1_ns:.1f}x faster" if result.l1_ns > 0 else "N/A"
    l2_speedup = f"{result.dram_ns / result.l2_ns:.1f}x faster" if result.l2_ns > 0 else "N/A"
    l3_speedup = f"{result.dram_ns / result.l3_ns:.1f}x faster" if result.l3_ns > 0 else "N/A"

    l1_gauge = build_gauge(l1_rel, width=10, mode="latency")
    l2_gauge = build_gauge(l2_rel, width=10, mode="latency")
    l3_gauge = build_gauge(l3_rel, width=10, mode="latency")
    dram_gauge = build_gauge(100.0, width=10, mode="latency")

    table.add_row("L1 Data Cache", l1_size_str, f"[bold bright_green]{result.l1_ns:.2f} ns[/bold bright_green]", l1_gauge, f"On-die L1 core data cache ([bold bright_green]{l1_speedup}[/bold bright_green] than DRAM)")
    table.add_row("L2 Dedicated Cache", l2_size_str, f"[bold bright_green]{result.l2_ns:.2f} ns[/bold bright_green]", l2_gauge, f"Per-core dedicated L2 cache ([bold bright_green]{l2_speedup}[/bold bright_green] than DRAM)")
    table.add_row("L3 Shared Smart Cache", l3_size_str, f"[bold bright_yellow]{result.l3_ns:.2f} ns[/bold bright_yellow]", l3_gauge, f"Shared LLC Smart Cache ([bold bright_yellow]{l3_speedup}[/bold bright_yellow] than DRAM)")
    table.add_row("Main System DRAM", dram_size_str, f"[bold bright_cyan]󰔛 {result.dram_ns:.2f} ns[/bold bright_cyan]", dram_gauge, "Random pointer-chasing baseline; includes TLB effects")

    console.print(table)


def render_results_table(results: list[TestResult], specs: HardwareSpecs):
    if not RICH_AVAILABLE:
        print("\n=== BENCHMARK RESULTS SUMMARY ===")
        for r in results:
            eff = f"{r.efficiency_pct:5.1f}%" if r.efficiency_pct is not None else "—"
            tp = f"{r.throughput_gb_s:7.2f} GB/s ({r.throughput_mib_s:9.1f} MiB/s)" if r.throughput_gb_s > 0 else "—"
            lat = f"{r.latency_ns:6.2f} ns" if r.latency_ns is not None else "—"
            print(
                f"{r.name:28s}: {tp} | {eff} of Max | Latency: {lat} | {r.details}"
            )
        return

    table = Table(
        title="[bold bright_cyan]󰓅 RAM Bandwidth & Latency Benchmark Summary[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        expand=True,
    )
    table.add_column("Benchmark Test Mode", style="bold bright_white", width=28)
    table.add_column("Throughput", justify="right", style="bold bright_green", width=14)
    table.add_column("Rate / Estimated Peak", justify="center", width=20)
    table.add_column("Access Latency", justify="right", style="bold bright_cyan", width=15)
    table.add_column("Test Configuration & Details", style="bright_white")

    for r in results:
        if r.name == "Random Memory Latency":
            tp_str = "[dim]—[/dim]"
            eff_str = "[dim]— (Pointer Chasing)[/dim]"
            lat_str = f"[bold bright_cyan]󰔛 {r.latency_ns:.2f} ns[/bold bright_cyan]" if r.latency_ns else "[dim]Failed[/dim]"
        else:
            tp_str = f"[bold bright_green]{r.throughput_gb_s:.2f} GB/s[/bold bright_green]" if r.throughput_gb_s > 0 else "[dim]Failed[/dim]"
            eff_str = build_gauge(r.efficiency_pct, width=10, mode="bandwidth")
            lat_str = "[dim]—[/dim]"

        table.add_row(
            r.name,
            tp_str,
            eff_str,
            lat_str,
            Text(r.details),
        )

    console.print(table)

    note_text = Text()
    note_text.append("󰨣 Microarchitectural Performance Insights:\n", style="bold bright_yellow")
    note_text.append(" 󰅂 ", style="bright_cyan")
    if specs.theoretical_max_gb_s:
        note_text.append("Theoretical Max Peak for your memory bus is ", style="bright_white")
        note_text.append(f"{specs.theoretical_max_gb_s:.2f} GB/s.\n", style="bold bright_green")
    else:
        note_text.append(
            "Estimated peak requires SMBIOS speed and user-supplied --channels.\n",
            style="bright_white",
        )

    if specs.configured_speed_mts and specs.factory_speed_mts and specs.factory_speed_mts > specs.configured_speed_mts:
        note_text.append(" 󰅂 ", style="bright_cyan")
        note_text.append("Frequency Downclocking Detected: ", style="bold bright_white")
        note_text.append(
            f"Installed RAM is factory-rated for {specs.factory_speed_mts} MT/s but currently operating at {specs.configured_speed_mts} MT/s (the firmware does not establish the cause).\n",
            style="bright_white",
        )

    single_core = next((r for r in results if r.name == "Single-Core Copy (1 Core)"), None)
    latency_res = next((r for r in results if r.name == "Random Memory Latency"), None)

    note_text.append(" 󰅂 ", style="bright_cyan")
    note_text.append("Single-Core Throughput Limit: ", style="bold bright_white")
    if single_core and single_core.throughput_gb_s > 0:
        note_text.append(
            f"Measured single-core copy throughput is {single_core.throughput_gb_s:.1f} GB/s — depends on cache residency, the copy implementation, and per-core memory parallelism.\n",
            style="bright_white",
        )
    else:
        note_text.append(
            f"Copy throughput on Core {specs.optimal_p_core} depends on cache residency, the copy implementation, and per-core memory parallelism.\n",
            style="bright_white",
        )
    note_text.append(" 󰅂 ", style="bright_cyan")
    note_text.append("Pure Read / Write Scaling: ", style="bold bright_white")
    peak_str = f"{specs.theoretical_max_gb_s:.1f} GB/s" if specs.theoretical_max_gb_s else "the memory bus peak"
    note_text.append(
        f"To approach {peak_str}, memory requests must be issued in parallel across multiple CPU cores ({specs.online_cpus} active).\n",
        style="bright_white",
    )
    note_text.append(" 󰅂 ", style="bright_cyan")
    note_text.append("Random Access Latency vs Bandwidth: ", style="bold bright_white")
    if latency_res and latency_res.latency_ns:
        note_text.append(
            f"Random pointer-chasing latency is {latency_res.latency_ns:.1f} ns. It includes cache, TLB, and scheduling effects; see the measured working set in the test details. ",
            style="bright_white",
        )
    else:
        note_text.append(
            "Random latency uses a randomized cache-line pointer cycle sized beyond the observed last-level cache. ",
            style="bright_white",
        )
    note_text.append(
        "Streaming bandwidth achieves far lower per-line cost via hardware parallelism.",
        style="bright_white",
    )

    panel = Panel(
        note_text,
        title="[bold bright_cyan]󰨣 Understanding Single-Thread vs Multi-Thread RAM Bandwidth & Latency[/bold bright_cyan]",
        border_style="bright_cyan",
    )
    console.print(panel)


HISTORY_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "dusky" / "settings" / "ram_test"
HISTORY_FILE = HISTORY_DIR / "history.json"


@contextlib.contextmanager
def atomic_text(path: Path, *, newline: str | None = None):
    """Publish a complete report and preserve the previous file on failure."""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline=newline,
                                     dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
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
def history_lock():
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with (HISTORY_DIR / ".lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def save_run_to_history(
    specs: HardwareSpecs, cache_hierarchy: CacheHierarchyResult | None,
    results: list[TestResult], args: argparse.Namespace,
) -> None:
    now_ts = time.strftime("%Y-%m-%d %H:%M:%S")
    time_short = time.strftime("%H:%M:%S")
    date_short = time.strftime("%m-%d")
    run_id = f"run_{time.time_ns()}"
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
        "cache": {
            "l1_ns": cache_hierarchy.l1_ns if cache_hierarchy else None,
            "l2_ns": cache_hierarchy.l2_ns if cache_hierarchy else None,
            "l3_ns": cache_hierarchy.l3_ns if cache_hierarchy else None,
            "dram_ns": cache_hierarchy.dram_ns if cache_hierarchy else None,
        } if cache_hierarchy else None,
        "metrics": metrics_map,
        "results_raw": [asdict(r) for r in results],
        "initial_temps": specs.initial_dram_temps,
        "final_temps": specs.final_dram_temps,
    }

    try:
        with history_lock():
            history = load_history()
            history.append(entry)
            atomic_json(HISTORY_DIR / f"{run_id}.json", entry)
            atomic_json(HISTORY_FILE, history[-100:])
            for discarded in history[:-100]:
                old_id = discarded.get("id", "")
                if re.fullmatch(r"run_[0-9_]+", old_id):
                    (HISTORY_DIR / f"{old_id}.json").unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        eprint(f"[Warning] Failed to save history: {exc}")


def load_history() -> list[dict]:
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
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError("Invalid historical numeric metric")
        for name in ("id", "timestamp", "time_short"):
            if name in entry and not isinstance(entry[name], str):
                raise ValueError(f"Invalid historical {name}")
    return data


def clear_history() -> None:
    with history_lock():
        for path in HISTORY_DIR.glob("run_*.json"):
            path.unlink()
        HISTORY_FILE.unlink(missing_ok=True)
    print(f"Cleared benchmark history in {HISTORY_DIR}")


def render_history_comparison(history: list[dict], count: int = 7) -> None:
    """Render a side-by-side comparison matrix of recent benchmark runs with best/worst scores highlighted."""
    if not history:
        msg = "No previous benchmark history found in ~/.config/dusky/settings/ram_test/"
        if RICH_AVAILABLE:
            console.print(f"[bold yellow]󰘓 {msg}[/bold yellow]")
        else:
            print(msg)
        return

    # Version 1 used different latency methods and incorrect copy/STREAM units.
    runs = [run for run in history if run.get("metrics_version") == 2][-count:]
    if not runs:
        print("No comparable runs using the current measurement method yet.")
        return

    metrics_meta = [
        ("Random DRAM Latency (ns)", "latency", "bold bright_cyan", lambda r: r.get("metrics", {}).get("Random Memory Latency (ns)") or (r.get("cache") or {}).get("dram_ns")),
        ("L1 Data Cache (ns)", "latency", "bold cyan", lambda r: (r.get("cache") or {}).get("l1_ns")),
        ("L2 Dedicated Cache (ns)", "latency", "bold cyan", lambda r: (r.get("cache") or {}).get("l2_ns")),
        ("L3 Smart Cache (ns)", "latency", "bold cyan", lambda r: (r.get("cache") or {}).get("l3_ns")),
        (None, None, None, None),
        ("Single-Core Copy (GB/s)", "bandwidth", "bold bright_yellow", lambda r: r.get("metrics", {}).get("Single-Core Copy (1 Core)")),
        ("Pure Read Multi-Core (GB/s)", "bandwidth", "bold bright_green", lambda r: r.get("metrics", {}).get("Pure Read (Multi-Thread)")),
        ("Pure Write Multi-Core (GB/s)", "bandwidth", "bold bright_green", lambda r: r.get("metrics", {}).get("Pure Write (Multi-Thread)")),
        ("STREAM Mix Multi-Core (GB/s)", "bandwidth", "bold bright_green", lambda r: r.get("metrics", {}).get("Stream Mix (Multi-Thread)")),
    ]

    if not RICH_AVAILABLE:
        print(f"\n=== MULTI-RUN BENCHMARK COMPARISON (Last {len(runs)} Runs) ===")
        header_cols = [f"{'Metric':<28s}"]
        for i, r in enumerate(runs):
            t_short = r.get("time_short", (r.get("timestamp", "").split() or ["?"])[-1])
            header_cols.append(f"R{i+1} ({t_short:>8s})")
        header_cols.extend([" Min / Max  ", " Δ vs Base "])
        header = " | ".join(header_cols)
        print(header)
        print("-" * len(header))
        for item in metrics_meta:
            if item[0] is None:
                print("-" * len(header))
                continue
            label, mode, label_style, extractor = item
            vals = [extractor(r) for r in runs if extractor(r) is not None]
            if not vals:
                continue
            row_items = [f"{label:<28s}"]
            for r in runs:
                v = extractor(r)
                if v is not None:
                    row_items.append(f"{v:>12.2f}" if v < 100 else f"{v:>12.1f}")
                else:
                    row_items.append(f"{'—':^12s}")
            min_v, max_v = min(vals), max(vals)
            min_max = f"{min_v:.2f} / {max_v:.2f}" if max_v < 10.0 else f"{min_v:.1f} / {max_v:.1f}"
            delta_str = "—"
            if len(vals) >= 2 and vals[0] != 0:
                pct_diff = ((vals[-1] - vals[0]) / vals[0]) * 100.0
                delta_str = f"{pct_diff:+.1f}%"
            row_items.append(f"{min_max:^12s}")
            row_items.append(f"{delta_str:>11s}")
            print(" | ".join(row_items))
        return

    t = Table(
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        show_header=True,
        expand=False,
    )
    t.add_column("Benchmark Metric", min_width=27, no_wrap=True)

    for i, r in enumerate(runs):
        is_latest = (i == len(runs) - 1)
        tag = "[bold bright_green]Latest[/bold bright_green]" if is_latest else f"R{i+1}"
        hp = " [dim](THP)[/dim]" if r.get("hugepages") else ""
        raw_t = r.get("time_short", (r.get("timestamp", "").split() or [""])[-1])
        t_short = ":".join(raw_t.split(":")[:2])
        t.add_column(f"{tag}{hp}\n[white]{t_short}[/white]", justify="right", min_width=7, no_wrap=True)

    t.add_column("Min / Max", justify="center", style="bold bright_white", min_width=13, no_wrap=True)
    t.add_column("Δ Base", justify="right", style="bold bright_cyan", min_width=9, no_wrap=True)

    for item in metrics_meta:
        if item[0] is None:
            t.add_section()
            continue
        label, mode, label_style, extractor = item
        vals = [extractor(r) for r in runs if extractor(r) is not None]
        if not vals:
            continue
        min_v, max_v = min(vals), max(vals)
        best_v = min_v if mode == "latency" else max_v
        worst_v = max_v if mode == "latency" else min_v

        row = [f"[{label_style}]{label}[/{label_style}]"]
        for r in runs:
            v = extractor(r)
            if v is not None:
                formatted = f"{v:.2f}" if v < 10 else f"{v:.1f}"
                if len(vals) > 1 and v == best_v and best_v != worst_v:
                    cell_str = f"[bold bright_green]{formatted}[/bold bright_green]"
                elif len(vals) > 1 and v == worst_v and best_v != worst_v:
                    cell_str = f"[bold bright_red]{formatted}[/bold bright_red]"
                else:
                    cell_str = f"[bright_white]{formatted}[/bright_white]"
                row.append(cell_str)
            else:
                row.append("[dim]—[/dim]")

        min_max = f"{min_v:.2f} / {max_v:.2f}" if max_v < 10.0 else f"{min_v:.1f} / {max_v:.1f}"
        delta_str = "—"
        if len(vals) >= 2 and vals[0] != 0:
            pct_diff = ((vals[-1] - vals[0]) / vals[0]) * 100.0
            if mode == "latency":
                color = "bright_green" if pct_diff < 0 else ("bright_red" if pct_diff > 0 else "white")
                arrow = "▼" if pct_diff < 0 else ("▲" if pct_diff > 0 else "")
            else:
                color = "bright_green" if pct_diff > 0 else ("bright_red" if pct_diff < 0 else "white")
                arrow = "▲" if pct_diff > 0 else ("▼" if pct_diff < 0 else "")
            delta_str = f"[{color}]{pct_diff:+.1f}% {arrow}[/{color}]"

        row.extend([min_max, delta_str])
        t.add_row(*row)

    console.print(f"\n[bold bright_cyan] 󰓅 Multi-Run Benchmark Comparison (Last {len(runs)} Runs)[/bold bright_cyan]")
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
        with atomic_text(export_path, newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["# SYSTEM HARDWARE METADATA"])
            writer.writerow(["# CPU Model", specs.cpu_model])
            writer.writerow(["# Memory Speed", f"{specs.mem_type} @ {specs.configured_speed_mts} MT/s"])
            writer.writerow(["# Topology", f"{specs.channels} Channels, {specs.bus_width_bits}-bit Bus Width"])
            writer.writerow([])

            writer.writerow(["Metric / Test", "Throughput (GB/s)", "Throughput (MiB/s)", "Efficiency (%)", "Latency (ns)", "Details"])
            for r in results:
                tp_gb = f"{r.throughput_gb_s:.2f}" if r.throughput_gb_s > 0 else "—"
                tp_mib = f"{r.throughput_mib_s:.1f}" if r.throughput_mib_s > 0 else "—"
                writer.writerow([r.name, tp_gb, tp_mib, f"{r.efficiency_pct:.1f}" if r.efficiency_pct is not None else "—", f"{r.latency_ns:.2f}" if r.latency_ns else "—", r.details])
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
        match = re.fullmatch(r"(\d+)(?:-(\d+)(?::([1-9]\d*))?)?", part)
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
        default=4096,
        help="Array size in MiB for mbw single-core test (default: 4096).",
    )
    parser.add_argument(
        "--cores",
        type=normalize_cores,
        help="Core range string to pin tests to (e.g. 0-13 or 0-7).",
    )
    parser.add_argument(
        "--hugepages",
        action="store_true",
        help="Request Transparent Huge Pages in latency tests; allocation depends on kernel policy.",
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
    parser.add_argument("--channels", type=positive_int, help="Known active channels; enables an estimated bus peak (SMBIOS slots are insufficient).")
    parser.add_argument("--channel-width", type=positive_int, default=64, help="Data bits per supplied channel (default: 64; use 32 for DDR5 subchannels).")
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

    if args.compare is not None and not any(arg == "--bench" or arg.startswith("--bench=") for arg in sys.argv[1:]):
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

        if args.channels:
            specs.channels = args.channels
            specs.bus_width_bits = args.channels * args.channel_width
            if specs.configured_speed_mts:
                specs.theoretical_max_gb_s = specs.configured_speed_mts * specs.bus_width_bits / 8000
        workers = args.workers or (len(args.cores.split(",")) if args.cores else specs.online_cpus)
        args.workers = workers
        results: list[TestResult] = []
        cache_hierarchy = None
        governor_ctx = set_cpu_performance() if not args.no_governor and has_sudo else contextlib.nullcontext(False)
        native_ctx = latency_binary() if args.bench in ("cache", "latency", "all") else contextlib.nullcontext(None)
        with governor_ctx as governor_active, native_ctx as binary:
            render_header(specs, governor_active=governor_active)
            progress_ctx = Progress(SpinnerColumn("dots", style="cyan"),
                                    TextColumn("{task.description}"), console=console, transient=True) if RICH_AVAILABLE else contextlib.nullcontext(None)
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
                                cache_hierarchy = run_cache_hierarchy_latency_test(args.cores, args.hugepages, args.samples, binary)
                            case "latency":
                                results.append(run_latency_test(128, specs, args.cores, args.hugepages, args.samples, binary))
                            case "single":
                                results.append(run_single_core_test(args.size, 10, args.time, specs, args.cores))
                            case "read":
                                results.append(run_pure_read_test(workers, args.time, specs, args.cores))
                            case "write":
                                results.append(run_pure_write_test(workers, args.time, specs, args.cores))
                            case "copy":
                                results.append(run_copy_stream_test(workers, args.time, specs, args.cores))
                    finally:
                        if progress:
                            progress.remove_task(task)
        # Restore policy controls before rendering and report I/O.
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
        (r.latency_ns is None or r.latency_ns <= 0) if r.name == "Random Memory Latency" else r.throughput_gb_s <= 0
        for r in results
    )
    if args.bench in ["cache", "all"] and cache_hierarchy is None:
        had_failure = True

    if had_failure:
        if RICH_AVAILABLE:
            console.print("[bold red]󰀨 One or more benchmarks failed — check warnings above.[/bold red]")
        else:
            print("One or more benchmarks failed — check warnings above.")
        return 1
    return 0


if __name__ == "__main__":
    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    sys.exit(main())
