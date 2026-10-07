#!/usr/bin/env python3
"""Real GTK construction, lazy expansion, search and reload stress (no actions).

Run from any directory with python3 audit/stress_startup.py --cycles 30.
Does not click command controls or start the control-center service.
"""
import argparse
import gc
import json
import logging
from pathlib import Path
import sys
import time
import weakref


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycles', type=int, default=30)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if args.cycles < 1:
        parser.error('--cycles must be positive')
    sys.path.insert(0, str(args.root.resolve()))
    import dusky_control_center as cc
    from lib import rows
    failures = []
    class Errors(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                failures.append(record.getMessage())
    logging.getLogger().addHandler(Errors())
    app = cc.DuskyControlCenter()
    app.set_flags(cc.Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    assert app._state.config_error is None, app._state.config_error
    context = cc.GLib.MainContext.default()
    def drain():
        # Bound iterations; a producer cannot keep a stress pass here forever.
        for _ in range(1000):
            if not context.pending():
                return
            context.iteration(False)
        raise AssertionError('Main loop failed to settle')
    def widgets(parent):
        yield parent
        child = parent.get_first_child()
        while child is not None:
            yield from widgets(child)
            child = child.get_next_sibling()
    def materialize(parent):
        if isinstance(parent, rows.ExpanderRow):
            parent.set_expanded(True)
        child = parent.get_first_child()
        while child is not None:
            materialize(child)
            child = child.get_next_sibling()
    def nested(layout, path, nav):
        count = 0
        for section in layout:
            for item in section.get('items', [section]):
                if item.get('type') == 'navigation':
                    next_path = [*path, item['properties']['title']]
                    ctx = app._get_context(nav, app._build_nav_page, next_path)
                    page = app._build_nav_page(next_path[-1], item.get('layout', []), ctx)
                    nav.push(page)
                    materialize(page)
                    count += 1 + nested(item.get('layout', []), next_path, nav)
                    nav.pop()
                elif item.get('type') == 'expander':
                    count += nested([{'items': item.get('items', [])}], path, nav)
        return count
    root_pages = nested_pages = 0
    references = []
    began = time.perf_counter()
    descriptors_before = len(list(Path('/proc/self/fd').iterdir()))
    try:
        for cycle in range(args.cycles):
            for index, config in enumerate(app._state.config['pages']):
                app._sidebar_list.select_row(app._sidebar_list.get_row_at_index(index))
                nav = app._stack.get_child_by_name(f'page-{index}')
                page = nav.get_visible_page()
                materialize(page)
                root_pages += 1
                nested_pages += nested(config['layout'], [config['title']], nav)
                references.extend(weakref.ref(widget) for widget in widgets(nav) if hasattr(widget, '_state'))
            for query in ('audio', 'tunnel', 'config', 'wallpaper', 'nonexistent setting', ''):
                app._execute_search(query)
            del nav, page
            app._clear_and_rebuild_ui(cycle % len(app._state.config['pages']))
            drain()
            gc.collect()
            assert not any(ref() is not None for ref in references), 'Removed rows survived rebuild'
            references.clear()
        # Exercise actual map/unmap, asynchronous Home queries and hidden idle.
        app._sidebar_list.select_row(app._sidebar_list.get_row_at_index(0))
        app._window.present()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            drain()
            time.sleep(.01)
        descriptors_mapped = len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(30):
            app._window.set_visible(False)
            app._window.unrealize()
            app._window.present()
            drain()
        app._window.set_visible(False)
        app._window.unrealize()
        drain()
        live = [widget for widget in widgets(app._stack) if hasattr(widget, '_state')]
        assert all(slot.source_id == 0 and slot.cancellable is None for widget in live for slot in widget._state._slots), 'Hidden observation sources survived'
        del live
        # Let cancelled async queries and file reads deliver guarded completions.
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            drain()
            time.sleep(.01)
        cpu_before = time.process_time()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            drain()
            time.sleep(.02)
        idle_cpu_ms = (time.process_time() - cpu_before) * 1000
        descriptors_after = len(list(Path('/proc/self/fd').iterdir()))
        assert descriptors_after <= descriptors_mapped, 'Descriptors grew across reopen cycles'
        assert not failures, failures
        print(json.dumps({'cycles': args.cycles, 'root_pages_built': root_pages,
            'nested_pages_built': nested_pages, 'reopen_cycles': 30,
            'hidden_idle_cpu_ms_over_3s': idle_cpu_ms,
            'fd_before': descriptors_before,
            'fd_after_first_map': descriptors_mapped,
            'fd_after': descriptors_after,
            'seconds': time.perf_counter() - began, 'errors': failures}, indent=2))
    finally:
        app._window.destroy()
        cc.utility.flush_settings()
        app.release()


if __name__ == '__main__':
    main()
