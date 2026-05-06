#!/usr/bin/env python3
"""
ram_latency.py

Pointer-chasing RAM latency benchmark for Linux.

What it measures:
  - ns per dependent load
  - across several working-set sizes so you can see cache-to-DRAM transitions

How it works:
  - Compiles a tiny C benchmark with cc/gcc/clang
  - Builds a single-cycle pointer chain using a full-period LCG permutation
  - Pins itself to one CPU when possible to reduce jitter
  - Runs several samples per size and reports median/min/max

Notes:
  - Lower is better.
  - Small sizes are mostly cache latency.
  - Larger sizes are closer to DRAM latency.
  - This is a latency benchmark, not a bandwidth benchmark.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent


DEFAULT_SIZES = ["64K", "512K", "4M", "32M", "128M"]
DEFAULT_SECONDS = 0.5
DEFAULT_SAMPLES = 5


C_SOURCE = r"""
#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <inttypes.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static volatile uint32_t sink;

static uint64_t now_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts) != 0) {
        perror("clock_gettime");
        exit(1);
    }
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void pin_to_first_allowed_cpu(void) {
#ifdef __linux__
    cpu_set_t allowed;
    CPU_ZERO(&allowed);

    if (sched_getaffinity(0, sizeof(allowed), &allowed) != 0) {
        return;
    }

    int first = -1;
    for (int cpu = 0; cpu < CPU_SETSIZE; cpu++) {
        if (CPU_ISSET(cpu, &allowed)) {
            first = cpu;
            break;
        }
    }

    if (first < 0) {
        return;
    }

    cpu_set_t pinned;
    CPU_ZERO(&pinned);
    CPU_SET(first, &pinned);

    (void)sched_setaffinity(0, sizeof(pinned), &pinned);
#endif
}

static size_t round_down_pow2(size_t x) {
    if (x == 0) {
        return 0;
    }
    size_t p = 1;
    while ((p << 1) <= x) {
        p <<= 1;
    }
    return p;
}

/*
  Full-period LCG modulo 2^k:
    x[n+1] = a*x[n] + c (mod 2^k)
  with:
    c odd
    a ≡ 1 (mod 4)
  This gives a single cycle over the entire power-of-two domain.
*/
static void build_cycle(uint32_t *next, uint32_t n) {
    const uint32_t a = 1664525u;      /* 1 mod 4 */
    const uint32_t c = 1013904223u;   /* odd */

    for (uint32_t i = 0; i < n; i++) {
        next[i] = (uint32_t)((uint64_t)i * (uint64_t)a + (uint64_t)c) & (n - 1);
    }
}

static double run_once(const uint32_t *next, uint32_t start, double seconds) {
    uint32_t idx = start;
    const size_t chunk = 8192;
    uint64_t steps = 0;

    const uint64_t begin = now_ns();
    const uint64_t deadline = begin + (uint64_t)(seconds * 1000000000.0);

    while (now_ns() < deadline) {
        for (size_t i = 0; i < chunk; i++) {
            idx = next[idx];
        }
        steps += chunk;
    }

    const uint64_t elapsed = now_ns() - begin;
    sink = idx;

    return (double)elapsed / (double)steps;
}

int main(int argc, char **argv) {
    if (argc != 5) {
        fprintf(stderr, "usage: %s <requested_bytes> <seconds> <samples> <pin 0|1>\n", argv[0]);
        return 2;
    }

    errno = 0;
    unsigned long long requested_ull = strtoull(argv[1], NULL, 10);
    double seconds = strtod(argv[2], NULL);
    long samples_l = strtol(argv[3], NULL, 10);
    long pin_l = strtol(argv[4], NULL, 10);

    if (errno != 0 || requested_ull == 0 || seconds <= 0.0 || samples_l <= 0) {
        fprintf(stderr, "invalid arguments\n");
        return 2;
    }

    size_t requested_bytes = (size_t)requested_ull;
    int samples = (int)samples_l;
    int pin = pin_l != 0;

    if (pin) {
        pin_to_first_allowed_cpu();
    }

    size_t element_count = requested_bytes / sizeof(uint32_t);
    element_count = round_down_pow2(element_count);

    if (element_count < 1024) {
        fprintf(stderr, "working set too small after rounding; use at least 4 KiB\n");
        return 2;
    }

    if (element_count > UINT32_MAX) {
        fprintf(stderr, "working set too large for this benchmark's 32-bit index format\n");
        return 2;
    }

    size_t actual_bytes = element_count * sizeof(uint32_t);

    uint32_t *next = malloc(actual_bytes);
    if (!next) {
        fprintf(stderr, "malloc(%zu) failed\n", actual_bytes);
        return 1;
    }

    build_cycle(next, (uint32_t)element_count);

    /*
      Warm up:
      touch the full chain once so the pages are resident before timing.
    */
    uint32_t idx = 0;
    for (size_t i = 0; i < element_count; i++) {
        idx = next[idx];
    }
    sink = idx;

    printf("RESULT\t%zu\t%zu", requested_bytes, actual_bytes);
    for (int i = 0; i < samples; i++) {
        double ns_per_access = run_once(next, 0, seconds);
        printf("\t%.6f", ns_per_access);
    }
    printf("\n");

    free(next);
    return 0;
}
"""


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def run_capture(cmd: list[str]) -> str:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=proc.stdout)
    return proc.stdout or ""


def find_compiler() -> str:
    for candidate in ("cc", "gcc", "clang"):
        try:
            subprocess.run(
                [candidate, "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            return candidate
        except Exception:
            pass
    raise SystemExit("No C compiler found. Install cc/gcc/clang and run again.")


def parse_size(text: str) -> int:
    s = text.strip().lower()

    units = {
        "b": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "mib": 1024**2,
        "g": 1024**3,
        "gb": 1024**3,
        "gib": 1024**3,
    }

    for suffix in ("kib", "mib", "gib", "kb", "mb", "gb", "k", "m", "g", "b"):
        if s.endswith(suffix):
            num = s[: -len(suffix)].strip()
            if not num:
                raise ValueError(f"Invalid size: {text!r}")
            return int(float(num) * units[suffix])

    return int(s)


def human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{n} B"


def compile_benchmark(tmpdir: Path) -> Path:
    c_path = tmpdir / "ram_latency.c"
    bin_path = tmpdir / "ram_latency"
    c_path.write_text(dedent(C_SOURCE), encoding="utf-8")

    compiler = find_compiler()
    cmd = [
        compiler,
        "-O3",
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-pedantic",
        str(c_path),
        "-o",
        str(bin_path),
    ]
    subprocess.run(cmd, check=True)
    return bin_path


def run_one(binary: Path, size_bytes: int, seconds: float, samples: int, pin: bool) -> tuple[int, int, list[float]]:
    proc = subprocess.run(
        [str(binary), str(size_bytes), f"{seconds:.6f}", str(samples), "1" if pin else "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )

    result_line = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT\t"):
            result_line = line.strip()
            break

    if result_line is None:
        raise RuntimeError(f"Could not find RESULT line in output:\n{proc.stdout}")

    parts = result_line.split("\t")
    if len(parts) < 4:
        raise RuntimeError(f"Malformed RESULT line:\n{result_line}")

    requested_bytes = int(parts[1])
    actual_bytes = int(parts[2])
    sample_values = [float(x) for x in parts[3:]]

    if len(sample_values) != samples:
        raise RuntimeError(
            f"Expected {samples} samples, got {len(sample_values)} in line:\n{result_line}"
        )

    return requested_bytes, actual_bytes, sample_values


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure RAM latency with a pointer-chasing benchmark."
    )
    parser.add_argument(
        "--sizes",
        nargs="*",
        default=DEFAULT_SIZES,
        help="Working-set sizes such as 64K 512K 4M 32M 128M.",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=DEFAULT_SECONDS,
        help="Seconds per sample for each size.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLES,
        help="Number of samples per size.",
    )
    parser.add_argument(
        "--no-pin",
        action="store_true",
        help="Do not pin the benchmark to a single CPU.",
    )
    args = parser.parse_args()

    try:
        sizes = [parse_size(s) for s in args.sizes]
    except ValueError as exc:
        eprint(exc)
        return 2

    with tempfile.TemporaryDirectory(prefix="ram-latency-") as td:
        tmpdir = Path(td)
        try:
            binary = compile_benchmark(tmpdir)
        except subprocess.CalledProcessError as exc:
            eprint("Compilation failed.")
            if exc.output:
                print(exc.output)
            return exc.returncode

        rows: list[tuple[int, int, list[float]]] = []
        for size_bytes in sizes:
            try:
                requested_bytes, actual_bytes, sample_values = run_one(
                    binary,
                    size_bytes,
                    args.seconds,
                    args.samples,
                    pin=not args.no_pin,
                )
                rows.append((requested_bytes, actual_bytes, sample_values))
            except subprocess.CalledProcessError as exc:
                eprint(f"Benchmark failed for {human_bytes(size_bytes)}.")
                if exc.output:
                    print(exc.output)
                return exc.returncode

    print()
    print(f"{'requested':>14}  {'actual':>14}  {'median ns':>12}  {'min':>10}  {'max':>10}")
    print("-" * 68)
    for requested_bytes, actual_bytes, sample_values in rows:
        med = statistics.median(sample_values)
        mn = min(sample_values)
        mx = max(sample_values)
        print(
            f"{human_bytes(requested_bytes):>14}  "
            f"{human_bytes(actual_bytes):>14}  "
            f"{med:12.3f}  {mn:10.3f}  {mx:10.3f}"
        )

    print()
    print("Lower is better.")
    print("Small working sets are mostly cache latency; large working sets are closer to DRAM latency.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
