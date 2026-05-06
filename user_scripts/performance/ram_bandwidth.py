#!/usr/bin/env python3
"""
ram_benchmark.py

Arch Linux RAM bandwidth helper.

What it does:
  - Installs missing benchmark packages with:
      paru -S --needed --noconfirm mbw stress-ng
    but only for the packages that are not already installed.
  - Prompts you to run:
      1) mbw
      2) stress-ng --stream
      3) both
  - Uses all online CPUs by default for stress-ng.
  - Parses stress-ng per-instance read/write rates and sums them.
  - Parses mbw averages and highlights MEMCPY.

Notes:
  - mbw is single-threaded.
  - stress-ng --stream is STREAM-like, not the official STREAM benchmark.
  - mbw reports MiB/s.
  - stress-ng reports MB/s in its per-instance lines.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass


MBW_PKG = "mbw"
STRESS_NG_PKG = "stress-ng"


@dataclass
class BenchResult:
    name: str
    raw_output: str


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def tool_exists(name: str) -> bool:
    return shutil.which(name) is not None


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


def online_cpus() -> int:
    """
    Prefer nproc because it respects the active CPU set.
    """
    if tool_exists("nproc"):
        try:
            return max(int(run_capture(["nproc"]).strip()), 1)
        except Exception:
            pass
    return max(os.cpu_count() or 1, 1)


def ensure_paru() -> None:
    if not tool_exists("paru"):
        raise SystemExit("paru was not found in PATH. Install paru first, then re-run this script.")


def install_missing_packages() -> None:
    """
    Install only the benchmark commands that are missing.
    """
    ensure_paru()

    missing: list[str] = []
    if not tool_exists(MBW_PKG):
        missing.append(MBW_PKG)
    if not tool_exists(STRESS_NG_PKG):
        missing.append(STRESS_NG_PKG)

    if not missing:
        return

    print(f"Installing missing package(s): {', '.join(missing)}")
    cmd = ["paru", "-S", "--needed", "--noconfirm", *missing]
    subprocess.run(cmd, check=True)

    still_missing = [pkg for pkg in missing if not tool_exists(pkg)]
    if still_missing:
        raise SystemExit(
            "Installation finished, but these commands are still missing from PATH: "
            + ", ".join(still_missing)
        )


def prompt_choice() -> str:
    print()
    print("Choose a benchmark:")
    print("  1) mbw (single-thread)")
    print("  2) stress-ng stream (multi-core)")
    print("  3) both")
    print("  q) quit")

    while True:
        choice = input("> ").strip().lower()
        if choice in {"1", "2", "3", "q", "quit", "exit"}:
            return choice
        print("Please enter 1, 2, 3, or q.")


def prompt_int(prompt: str, default: int, minimum: int = 1) -> int:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if raw == "":
            return default
        try:
            value = int(raw)
            if value < minimum:
                raise ValueError
            return value
        except ValueError:
            print(f"Enter an integer >= {minimum}.")


def prompt_text(prompt: str, default: str) -> str:
    raw = input(f"{prompt} [{default}]: ").strip()
    return raw or default


def mib_to_gib(mib: float) -> float:
    return mib / 1024.0


def mb_to_gb(mb: float) -> float:
    return mb / 1000.0


def run_mbw() -> BenchResult:
    print()
    print("mbw: single-thread bandwidth test")
    size_mib = prompt_int("Array size in MiB", 4096, minimum=1)
    runs = prompt_int("Runs per test", 10, minimum=1)

    cmd = ["mbw", "-n", str(runs), str(size_mib)]
    print()
    print("Running:", " ".join(cmd))
    output = run_capture(cmd)

    print(output, end="" if output.endswith("\n") else "\n")

    avg_re = re.compile(
        r"^AVG\s+Method:\s+(\S+)\s+Elapsed:\s+[0-9.]+\s+MiB:\s+[0-9.]+\s+Copy:\s+([0-9.]+)\s+MiB/s\s*$",
        re.MULTILINE,
    )
    averages = avg_re.findall(output)

    if averages:
        print()
        print("Parsed averages:")
        for method, copy_mib_s in averages:
            mib_s = float(copy_mib_s)
            gib_s = mib_to_gib(mib_s)
            print(f"  {method:8s}: {mib_s:10.3f} MiB/s   ({gib_s:8.3f} GiB/s)")

        memcpy = next((float(rate) for method, rate in averages if method == "MEMCPY"), None)
        if memcpy is not None:
            print()
            print(f"Primary mbw result (MEMCPY): {memcpy:.3f} MiB/s ({mib_to_gib(memcpy):.3f} GiB/s)")
    else:
        print("Could not parse mbw averages from output.")

    return BenchResult(name="mbw", raw_output=output)


def run_stress_ng() -> BenchResult:
    print()
    print("stress-ng stream: multi-core STREAM-like bandwidth test")
    default_workers = online_cpus()
    workers = prompt_int("Workers (defaults to all online CPUs)", default_workers, minimum=1)
    timeout = prompt_text("Timeout", "10s")

    cmd = [
        "stress-ng",
        "--stream",
        str(workers),
        "--timeout",
        timeout,
        "--metrics-brief",
        "-v",
    ]

    print()
    print("Running:", " ".join(cmd))
    output = run_capture(cmd)

    print(output, end="" if output.endswith("\n") else "\n")

    rate_re = re.compile(
        r"memory rate:\s+([0-9]+(?:\.[0-9]+)?)\s+MB read/sec,\s+([0-9]+(?:\.[0-9]+)?)\s+MB write/sec"
    )
    matches = rate_re.findall(output)

    if not matches:
        print("Could not find stress-ng per-instance memory-rate lines to sum.")
        return BenchResult(name="stress-ng", raw_output=output)

    total_read_mb_s = sum(float(read) for read, _ in matches)
    total_write_mb_s = sum(float(write) for _, write in matches)
    total_mb_s = total_read_mb_s + total_write_mb_s

    print()
    print("Summed from per-instance stress-ng output:")
    print(f"  Read : {total_read_mb_s:10.2f} MB/s   ({mb_to_gb(total_read_mb_s):8.2f} GB/s)")
    print(f"  Write: {total_write_mb_s:10.2f} MB/s   ({mb_to_gb(total_write_mb_s):8.2f} GB/s)")
    print(f"  Total: {total_mb_s:10.2f} MB/s   ({mb_to_gb(total_mb_s):8.2f} GB/s)")

    if len(matches) != workers:
        print()
        print(f"Warning: parsed {len(matches)} worker rate lines, expected {workers}.")

    return BenchResult(name="stress-ng", raw_output=output)


def main() -> int:
    try:
        install_missing_packages()
    except subprocess.CalledProcessError as exc:
        eprint(f"Package installation failed with exit code {exc.returncode}.")
        if exc.output:
            print(exc.output)
        return exc.returncode
    except SystemExit as exc:
        eprint(exc)
        return 1

    while True:
        choice = prompt_choice()

        if choice == "q":
            return 0

        try:
            if choice == "1":
                run_mbw()
            elif choice == "2":
                run_stress_ng()
            elif choice == "3":
                run_mbw()
                print()
                run_stress_ng()
        except subprocess.CalledProcessError as exc:
            eprint(f"Benchmark failed with exit code {exc.returncode}.")
            if exc.output:
                print(exc.output)
            return exc.returncode

        return 0


if __name__ == "__main__":
    raise SystemExit(main())
