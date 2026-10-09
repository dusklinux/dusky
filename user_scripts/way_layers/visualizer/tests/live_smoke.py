#!/usr/bin/env python3
"""Live Wayland smoke test with temporary config and no compositor rule changes.

Run from a Hyprland session: python3 tests/live_smoke.py
Briefly displays each style and an overlay. Requires Cava and PyOpenGL.
"""

import importlib.util
import logging
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="visualizer-live-") as tmp:
        os.environ["XDG_CONFIG_HOME"] = str(Path(tmp) / "config")
        os.environ["XDG_CACHE_HOME"] = str(Path(tmp) / "cache")
        spec = importlib.util.spec_from_file_location("visualizer_smoke", ROOT / "visualizer_daemon.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        logging.basicConfig(level=logging.INFO)
        m.LOCK_FILE = Path(tmp) / "instance.lock"
        m.LAYER_NAMESPACE = "dusky-visualizer-audit"
        app = m.Visualizer()
        app.apply_hyprland_rules = lambda: None
        failures = []
        pixels_by_style = {}
        audio_reads = 0
        stage = 0
        original_gl = app.on_gl_render
        original_audio = app.on_cava_stdout

        def render(widget, context):
            result = original_gl(widget, context)
            key = str(app.config.style)
            # One visible sample per style verifies rendering without continuous
            # GPU readback and large temporary allocations on every frame.
            if pixels_by_style.get(key, 0):
                return result
            try:
                widget.make_current()
                widget.attach_buffers()
                scale = widget.get_scale_factor()
                pixels = m.GL.glReadPixels(0, 0, widget.get_allocated_width() * scale,
                    widget.get_allocated_height() * scale, m.GL.GL_RGBA, m.GL.GL_UNSIGNED_BYTE)
                assert m.GL.glGetError() == m.GL.GL_NO_ERROR
                pixels_by_style[key] = sum(1 for value in pixels[3::4] if value)
            except Exception as exc:
                failures.append(exc)
            return result

        def audio(fd, condition):
            nonlocal audio_reads
            result = original_audio(fd, condition)
            if result:
                audio_reads += 1
            return result

        app.on_gl_render = render
        app.on_cava_stdout = audio
        styles = list(m.Style)

        def step():
            nonlocal stage
            try:
                if stage == 0:
                    assert app.use_gl and app.gl_program is not None and not app.gl_failed
                    assert app.cava_proc.poll() is None and audio_reads > 0
                    app.widget.make_current()
                    print("OpenGL:", m.GL.glGetString(m.GL.GL_VERSION).decode())
                    print("PipeWire Cava reads:", audio_reads)
                    print("Surface:", app.widget.get_allocated_width(), app.widget.get_allocated_height(),
                        "monitor height:", app.monitor.get_geometry().height)
                elif 1 <= stage <= len(styles):
                    app.config.style = styles[stage - 1]
                    app.has_rendered_idle_clear = False
                    app.setup_window()
                    app.ensure_tick()
                elif stage == len(styles) + 1:
                    assert not app.gl_failed
                    assert len(pixels_by_style) == len(styles) and all(pixels_by_style.values())
                    app.config.gpu_acceleration = False
                    app.setup_window()
                    app.ensure_tick()
                    assert not app.use_gl
                elif stage == len(styles) + 2:
                    fd = os.open(m.CTL_FILE, os.O_WRONLY | os.O_NONBLOCK)
                    try:
                        os.write(fd, b"over")
                        os.write(fd, b"lay\n")
                    finally:
                        os.close(fd)
                elif stage == len(styles) + 3:
                    assert app.is_overlay
                    app.toggle_enabled()
                    assert app.window is None and app.cava_proc is None and app.tick_source is None
                    app.toggle_enabled()
                    assert app.window is not None and app.cava_proc is not None
                    app.config.gpu_acceleration = True
                    app.save_config()
                    app.gl_failed = False
                    app.setup_window()
                    app.ensure_tick()
                elif stage == len(styles) + 4:
                    assert app.use_gl and app.gl_program is not None
                    app.gl_failed = True
                    app.schedule_cairo_fallback()
                elif stage == len(styles) + 5:
                    assert not app.use_gl
                    m.Gtk.main_quit()
                    return False
                stage += 1
            except Exception as exc:
                failures.append(exc)
                m.Gtk.main_quit()
                return False
            return True

        try:
            assert app.check_display()
            app.acquire_lock()
            app.load_initial()
            app.init_fifo_ipc()
            app.init_file_monitors()
            app.apply_config_changes(None)
            m.GLib.timeout_add(750, step)
            m.Gtk.main()
        finally:
            app.shutdown()
        if failures:
            raise failures[0]
        print("GPU visible pixel counts:", pixels_by_style)
        print("PASS: live styles, PipeWire frames, FIFO, toggle, renderer switch, GPU failure recovery")


if __name__ == "__main__":
    main()
