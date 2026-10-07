#!/usr/bin/env python3
"""Measure fresh Wayland processes through first GTK paint; never activates systemd.

Existing caches: python3 audit/benchmark_startup.py --runs 20
Empty application/bytecode/driver caches: add --empty-cache (not a cold boot).
Deterministic profile: add --profile /tmp/startup.prof --runs 1.
Use --root to compare a preserved source snapshot with the same harness.
"""
import argparse
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tempfile
import time


def child(root: Path, profile: str | None) -> None:
    started = time.perf_counter()
    profiler = None
    if profile:
        import cProfile
        profiler = cProfile.Profile()
        profiler.enable()
    sys.path.insert(0, str(root))
    sys.argv = [str(root / 'dusky_control_center.py')]
    import dusky_control_center as cc
    imported = time.perf_counter()
    app = cc.DuskyControlCenter()
    app.set_flags(cc.Gio.ApplicationFlags.NON_UNIQUE)
    stages = {}
    for name in ('_load_config_and_css_sync', '_apply_css', '_build_ui'):
        original = getattr(app, name)
        def timed(*args, _original=original, _name=name, **kwargs):
            before = time.perf_counter()
            result = _original(*args, **kwargs)
            stages[_name] = (time.perf_counter() - before) * 1000
            return result
        setattr(app, name, timed)
    app.register(None)
    registered = time.perf_counter()
    window = app._window
    painted_once = False

    def painted(clock):
        nonlocal painted_once
        if painted_once:
            return
        painted_once = True
        if profiler:
            profiler.disable()
            profiler.dump_stats(profile)
        stages.update(
            import_ms=(imported - started) * 1000,
            ready_ms=(registered - started) * 1000,
            paint_ms=(time.perf_counter() - started) * 1000,
            rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            renderer=type(window.get_renderer()).__name__,
        )
        print(json.dumps(stages), flush=True)
        cc.GLib.idle_add(app.quit, priority=cc.GLib.PRIORITY_HIGH)

    def mapped(widget):
        stages['map_ms'] = (time.perf_counter() - started) * 1000
        widget.get_frame_clock().connect('after-paint', painted)

    window.connect('map', mapped)
    app.run([])
    if not painted_once:
        raise RuntimeError('Application exited before its first paint')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--runs', type=int, default=20)
    parser.add_argument('--empty-cache', action='store_true')
    parser.add_argument('--profile', help='Save an instrumented cProfile run to this file')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error('--runs must be positive')
    if args.child:
        child(args.root.resolve(), args.profile)
        return
    runs = []
    for _ in range(args.runs):
        with tempfile.TemporaryDirectory(prefix='dusky-startup-') as directory:
            environment = os.environ.copy()
            command = [sys.executable]
            if args.empty_cache:
                environment['XDG_CACHE_HOME'] = directory
                command += ['-X', f'pycache_prefix={directory}/duskycc']
            command += [str(Path(__file__).resolve()), '--child', '--root', str(args.root.resolve())]
            if args.profile:
                command += ['--profile', args.profile]
            started = time.perf_counter()
            result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30, check=True)
            elapsed = (time.perf_counter() - started) * 1000
            sample = json.loads(result.stdout)
            sample['process_ms'] = elapsed
            runs.append(sample)
            if result.stderr:
                print(result.stderr, file=sys.stderr, end='')
    numeric = [key for key in runs[0] if isinstance(runs[0][key], (float, int))]
    print(json.dumps({
        'root': str(args.root.resolve()), 'runs': runs,
        'cache': 'empty' if args.empty_cache else 'existing',
        'instrumented': bool(args.profile),
        'median': {key: statistics.median(sample[key] for sample in runs) for key in numeric},
        'range': {key: [min(sample[key] for sample in runs), max(sample[key] for sample in runs)] for key in numeric},
    }, indent=2))


if __name__ == '__main__':
    main()
