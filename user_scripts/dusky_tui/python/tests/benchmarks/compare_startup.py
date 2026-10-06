#!/usr/bin/env python3
"""Alternate fresh before/after processes for shared imports and router startup.

These measure process completion, not terminal presentation. Bytecode and
filesystem caches are warmed deliberately; first invocations are kept separately.
Use benchmark_startup.py for launcher lifecycle spans and tab readiness.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter_ns


def measure(command: list[str]) -> float:
    start = perf_counter_ns()
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    elapsed = (perf_counter_ns() - start) / 1e6
    if result.returncode:
        raise RuntimeError(f"{command!r} failed ({result.returncode}): {result.stderr.decode(errors='replace')}")
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True, help="Saved unmodified launcher")
    parser.add_argument("--after", type=Path, default=Path(__file__).resolve().parents[2] / "main/main.py")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--schema", type=Path, default=Path(__file__).with_name("fixture.py"))
    parser.add_argument("--runs", type=int, default=31)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.runs < 2 or args.warmup < 0:
        parser.error("--runs must be at least 2 and --warmup nonnegative")

    commands = {}
    fingerprints = {}
    for label, launcher in (("before", args.before.resolve()), ("after", args.after.resolve())):
        root = launcher.parents[2]
        prefix = f"import sys; sys.path.insert(0, {str(root)!r}); "
        commands[label] = {
            "help": [args.python, str(launcher), "--help"],
            "export_docs": [args.python, str(launcher), str(args.schema.resolve()), "--export-docs"],
            "core_import": [args.python, "-c", prefix + "import python.frontend.core_types"],
            "ui_import": [args.python, "-c", prefix + "import python.frontend.ui"],
        }
        fingerprints[label] = {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (launcher, root / "python/frontend/core_types.py", root / "python/frontend/ui.py")
        }

    workloads = {}
    for workload in commands["before"]:
        initial = {label: measure(commands[label][workload]) for label in commands}
        samples = {label: [] for label in commands}
        for run in range(args.warmup + args.runs):
            # Alternate order to reduce bias from changing host conditions.
            labels = ("before", "after") if run % 2 == 0 else ("after", "before")
            for label in labels:
                duration = measure(commands[label][workload])
                if run >= args.warmup:
                    samples[label].append(duration)
        medians = {label: statistics.median(values) for label, values in samples.items()}
        change = (medians["before"] - medians["after"]) / medians["before"] * 100
        workloads[workload] = {
            "commands": {label: commands[label][workload] for label in commands},
            "initial_invocation_ms": initial,
            "samples_ms": samples,
            "median_ms": medians,
            "quartiles_ms": {label: statistics.quantiles(values, n=4) for label, values in samples.items()},
            "median_reduction_percent": change,
        }
        print(f"{workload:14} {medians['before']:8.2f} -> {medians['after']:8.2f} ms ({change:+.1f}% reduction)")

    version = subprocess.run([args.python, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    args.output.write_text(json.dumps({
        "python": version, "runs_per_variant": args.runs, "warmup_pairs": args.warmup,
        "cache_conditions": "warm filesystem/bytecode; no cache flushing; first invocations recorded separately",
        "source_sha256": fingerprints, "workloads": workloads,
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
