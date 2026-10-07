#!/usr/bin/env python3
"""Measure pre-open service memory and activation-to-paint in isolated GTK processes.

Uses service-mode startup without owning the production D-Bus name or starting
systemd. --root compares a source snapshot using the identical harness.
Memory is current /proc smaps_rollup, in KiB, rather than peak RSS.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time


def child(root: Path) -> None:
    started = time.perf_counter()
    sys.path.insert(0, str(root))
    import dusky_control_center as cc
    app = cc.DuskyControlCenter()
    app.set_flags(cc.Gio.ApplicationFlags.NON_UNIQUE | cc.Gio.ApplicationFlags.IS_SERVICE)
    samples = {}
    activation_started = 0.0
    generation = 0
    painted_generation = -1

    def memory():
        values = {}
        for line in Path('/proc/self/smaps_rollup').read_text(encoding='utf-8').splitlines():
            key, separator, value = line.partition(':')
            if separator and key in ('Rss', 'Pss', 'Private_Dirty', 'Anonymous', 'Swap'):
                values[key] = int(value.split()[0])
        return values

    def mapped(window):
        window.get_frame_clock().connect('after-paint', painted)

    def painted(clock):
        nonlocal painted_generation
        if painted_generation == generation:
            return
        painted_generation = generation
        samples.setdefault('activation_paint_ms', []).append(
            (time.perf_counter() - activation_started) * 1000)
        cc.GLib.timeout_add(500, hide if generation == 0 else done)

    build = app._build_ui
    def build_and_observe():
        build()
        app._window.connect('map', mapped)
    app._build_ui = build_and_observe

    def activate():
        nonlocal activation_started
        activation_started = time.perf_counter()
        app.do_activate()
        return cc.GLib.SOURCE_REMOVE

    def pre_open():
        samples['before_first_open'] = memory()
        samples['window_prebuilt'] = app._window is not None
        samples['rows_imported_before_open'] = 'lib.rows' in sys.modules
        cc.GLib.timeout_add(100, activate)
        return cc.GLib.SOURCE_REMOVE

    def hide():
        nonlocal generation
        samples['visible'] = memory()
        app._window.close()
        assert not app._window.get_visible()
        generation += 1
        cc.GLib.timeout_add(600, after_hide)
        return cc.GLib.SOURCE_REMOVE

    def after_hide():
        samples['after_hide'] = memory()
        cc.GLib.timeout_add(100, activate)
        return cc.GLib.SOURCE_REMOVE

    def done():
        samples['reopened'] = memory()
        print(json.dumps(samples), flush=True)
        app.quit()
        return cc.GLib.SOURCE_REMOVE

    app.register(None)
    samples['ready_ms'] = (time.perf_counter() - started) * 1000
    cc.GLib.timeout_add(600, pre_open)
    app.run([])
    if 'reopened' not in samples:
        raise RuntimeError('Service-mode lifecycle did not complete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--runs', type=int, default=10)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error('--runs must be positive')
    if args.child:
        child(args.root.resolve())
        return
    environment = dict(os.environ, PYTHONOPTIMIZE='2', MALLOC_ARENA_MAX='2')
    runs = []
    for _ in range(args.runs):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
            '--child', '--root', str(args.root.resolve())], env=environment,
            capture_output=True, text=True, timeout=20, check=True)
        runs.append(json.loads(result.stdout))
        if result.stderr:
            print(result.stderr, file=sys.stderr, end='')
    print(json.dumps({'root': str(args.root.resolve()), 'runs': runs, 'median': {
        'ready_ms': statistics.median(r['ready_ms'] for r in runs),
        'first_activation_paint_ms': statistics.median(r['activation_paint_ms'][0] for r in runs),
        'reopen_paint_ms': statistics.median(r['activation_paint_ms'][1] for r in runs),
        **{phase: {key: statistics.median(r[phase][key] for r in runs)
            for key in runs[0][phase]} for phase in
            ('before_first_open', 'visible', 'after_hide', 'reopened')},
    }}, indent=2))


if __name__ == '__main__':
    main()
