"""Focused regressions; run with python3 -m unittest discover -s tests -v."""

import errno
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


m = load_module("visualizer_test_daemon", ROOT / "visualizer_daemon.py")
ctl = load_module("visualizer_test_ctl", ROOT / "visualizer_ctl.py")


class VisualizerTests(unittest.TestCase):
    def setUp(self):
        self.app = m.Visualizer()
        self.app.ensure_data_arrays()
        self.app.ensure_tick = lambda: None

    def tearDown(self):
        self.app.shutdown()

    def test_gpu_setting_change_clears_failure_while_disabled_or_enabling(self):
        self.app.setup_window = lambda: None
        self.app.start_cava = lambda: True
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                old = m.replace(self.app.config, enabled=False, gpu_acceleration=False)
                self.app.config = m.replace(old, enabled=enabled, gpu_acceleration=True)
                self.app.gl_failed = True
                self.app.apply_config_changes(old)
                self.assertFalse(self.app.gl_failed)

    def test_destroy_window_cancels_its_pending_fallback(self):
        self.app.schedule_cairo_fallback()
        source = self.app.fallback_source
        self.assertIsNotNone(m.GLib.MainContext.default().find_source_by_id(source))
        self.app.destroy_window()
        self.assertIsNone(self.app.fallback_source)
        self.assertFalse(self.app.fallback_pending)
        self.assertIsNone(m.GLib.MainContext.default().find_source_by_id(source))

    def test_gpu_fallback_runs_while_default_priority_work_is_ready(self):
        recovered = []
        def fallback():
            recovered.append(True)
            return False
        self.app.fallback_to_cairo = fallback
        busy = m.GLib.timeout_add(0, lambda: True)
        try:
            self.app.schedule_cairo_fallback()
            for _ in range(10):
                m.GLib.MainContext.default().iteration(False)
            self.assertTrue(recovered, "GPU recovery was starved by normal event-loop work")
        finally:
            self.app.remove_glib_source(busy)

    def test_bar_count_change_recalculates_window_geometry(self):
        old = self.app.config
        self.app.config = m.replace(old, bars=16)
        self.app.start_cava = lambda: True
        with patch.object(self.app, "setup_window") as setup:
            self.app.apply_config_changes(old)
        setup.assert_called_once_with()

    def test_missing_files_retain_current_settings_on_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(m, "CONFIG_FILE", Path(tmp) / "missing.json"), \
                 patch.object(m, "COLORS_FILE", Path(tmp) / "missing-colors.json"):
                self.app.config.enabled = False
                self.app.config.gain = 3
                self.app.colors.accent = "#123456"
                self.app.apply_config_changes = lambda old: None
                self.app.execute_reload()
                self.assertFalse(self.app.config.enabled)
                self.assertEqual(self.app.config.gain, 3)
                self.assertEqual(self.app.colors.accent, "#123456")

    def test_config_bounds_and_round_trip(self):
        config = m.Config.from_dict({"bars": 17, "inner_glow": 9,
            "specular_shine": -1, "stardust": 5, "fps": "nan",
            "cava_lower_freq": 99999, "cava_upper_freq": 5,
            "cava_source": "bad\n[output]", "height_pct": "inf"})
        self.assertEqual(config.bars, 16)
        self.assertEqual((config.inner_glow, config.specular_shine, config.stardust), (1, 0, 1))
        self.assertLess(config.cava_lower_freq, config.cava_upper_freq)
        self.assertEqual(config.cava_source, "")
        self.assertEqual(config, m.Config.from_dict(config.to_dict()))

    def test_save_preserves_extensions_and_refuses_invalid_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            with patch.object(m, "CONFIG_FILE", path):
                extension = {"text": " keep whitespace ", "nested": [1, 2]}
                path.write_text(json.dumps({"extension": extension}))
                self.app.save_config()
                self.assertEqual(json.loads(path.read_text())["extension"], extension)
                path.write_text("broken")
                self.app.save_config()
                self.assertEqual(path.read_text(), "broken")

    def test_toggle_preserves_pending_external_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            self.app.config.enabled = False
            path.write_text(json.dumps({"enabled": False, "gain": 3, "bars": 16}))
            with patch.object(m, "CONFIG_FILE", path), patch.object(self.app, "apply_config_changes"):
                self.app.toggle_enabled()
            saved = json.loads(path.read_text())
            self.assertTrue(saved["enabled"])
            self.assertEqual((saved["gain"], saved["bars"]), (3, 16))
            self.assertEqual(len(self.app.cava_shared_data), 16)

    def test_enable_is_idempotent_across_pending_external_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            self.app.config.enabled = False
            path.write_text(json.dumps({"enabled": True, "gain": 3}))
            r, w = os.pipe()
            try:
                with patch.object(m, "CONFIG_FILE", path), patch.object(self.app, "apply_config_changes") as apply:
                    os.write(w, b"enable\nenable\n")
                    self.app.on_fifo_read(r, m.GLib.IOCondition.IN)
                    apply.assert_called_once()
            finally:
                os.close(r)
                os.close(w)
            self.assertTrue(self.app.config.enabled)
            self.assertEqual(self.app.config.gain, 3)

    def test_commands_persist_when_pending_edit_returns_to_current_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for enabled in (False, True):
                with self.subTest(enabled=enabled):
                    self.app.config.enabled = enabled
                    saved = self.app.config.to_dict()
                    saved["enabled"] = not enabled
                    path.write_text(json.dumps(saved))
                    with patch.object(m, "CONFIG_FILE", path), patch.object(self.app, "apply_config_changes") as apply:
                        if enabled:
                            self.app.set_enabled(True)
                        else:
                            self.app.toggle_enabled()
                        apply.assert_not_called()
                    self.assertEqual(json.loads(path.read_text())["enabled"], enabled)

    def test_save_removes_known_aliases_without_changing_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"enabled": True, " enabled ": False, " extension ": " keep "}))
            with patch.object(m, "CONFIG_FILE", path), patch.object(self.app, "apply_config_changes"):
                self.app.config = m.Config.from_dict(m.load_json_dict(path))
                self.app.toggle_enabled()
                saved = json.loads(path.read_text())
                self.assertTrue(m.Config.from_dict(saved).enabled)
                self.assertNotIn(" enabled ", saved)
                self.assertEqual(saved[" extension "], " keep ")

    def test_deploy_removes_known_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"enabled": True, " enabled ": False}))
            with patch.object(m, "CONFIG_DIR", path.parent), patch.object(m, "CONFIG_FILE", path):
                m.deploy_config()
            saved = json.loads(path.read_text())
            self.assertFalse(saved["enabled"])
            self.assertNotIn(" enabled ", saved)

    def test_shutdown_removes_signal_sources(self):
        self.app.install_signal_handlers()
        sources = self.app.signal_sources[:]
        self.assertEqual(len(sources), 3)
        self.app.shutdown()
        self.assertEqual(self.app.signal_sources, [])
        for source in sources:
            self.assertIsNone(m.GLib.MainContext.default().find_source_by_id(source))

    def test_model_fields_still_accept_whitespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "values.json"
            path.write_text(json.dumps({" style ": " dots ", " c1 ": " #123456 "}))
            raw = m.load_json_dict(path)
            self.assertEqual(m.Config.from_dict(raw).style, m.Style.DOTS)
            self.assertEqual(m.Colors.from_dict(raw).c1, "#123456")

    def test_gl_ramp_upload_only_on_change(self):
        widget = Mock()
        widget.get_allocated_width.return_value = 320
        widget.get_allocated_height.return_value = 180
        self.app.gl_program = 1
        self.app.gl_uniforms = {"u_ramp": 2}
        self.app.prepare_render_data = lambda: [0.5] * self.app.config.bars
        self.app.upload_gl_data = lambda data, n: None
        def upload():
            self.app.ramp_dirty = False
        self.app.upload_gl_ramp = upload
        # Avoid needing a hardware context or an installed optional package.
        self.app.gl_ramp_array = object()
        with patch.object(m, "GL") as gl:
            self.app.on_gl_render(widget, None)
            self.app.on_gl_render(widget, None)
            self.assertEqual(gl.glUniform4fv.call_count, 1)
            self.app.ramp_dirty = True
            self.app.on_gl_render(widget, None)
            self.assertEqual(gl.glUniform4fv.call_count, 2)
        self.app.gl_program = None

    def test_gain_saturates_audio_within_surface(self):
        self.app.config.gain = 5
        r, w = os.pipe()
        try:
            os.write(w, b"1000;" * self.app.config.bars + b"\n")
            self.app.on_cava_stdout(r, m.GLib.IOCondition.IN)
            self.assertEqual(self.app.cava_shared_data, [1.0] * self.app.config.bars)
        finally:
            os.close(r)
            os.close(w)

    def test_perimeter_uses_last_band_when_count_not_divisible_by_four(self):
        self.app.config.bars = 18
        self.app.config.style = m.Style.PERIMETER
        self.app.content_height = 100
        def render(last):
            surface = m.cairo.ImageSurface(m.cairo.FORMAT_ARGB32, 320, 180)
            self.app.draw_cairo(m.cairo.Context(surface), 320, 180, [0.0] * 17 + [last])
            surface.flush()
            return bytes(surface.get_data())
        self.assertTrue(render(0.0) != render(1.0), "Last spectrum band was ignored")

    def test_fifo_fragmentation_batch_and_idempotent_enable(self):
        read_fd, write_fd = os.pipe()
        calls = []
        self.app.toggle_enabled = lambda: calls.append("toggle")
        self.app.toggle_overlay = lambda: calls.append("overlay")
        def enable(enabled):
            if not self.app.config.enabled:
                calls.append("enable")
            self.app.config.enabled = enabled
        self.app.set_enabled = enable
        try:
            os.write(write_fd, b"tog")
            self.app.on_fifo_read(read_fd, m.GLib.IOCondition.IN)
            self.assertEqual(calls, [])
            os.write(write_fd, b"gle\noverlay\nenable\nunknown\n")
            self.app.on_fifo_read(read_fd, m.GLib.IOCondition.IN)
            self.assertEqual(calls, ["toggle", "overlay"])
            self.app.config.enabled = False
            os.write(write_fd, b"enable\n")
            self.app.on_fifo_read(read_fd, m.GLib.IOCondition.IN)
            self.assertEqual(calls[-1], "enable")
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_cava_fragmentation_newest_frame_and_low_signal(self):
        r, w = os.pipe()
        n = self.app.config.bars
        frame = (b"500;" * n) + b"\n"
        try:
            os.write(w, frame[:23])
            self.app.on_cava_stdout(r, m.GLib.IOCondition.IN)
            self.assertFalse(any(self.app.cava_shared_data))
            os.write(w, frame[23:] + b"250;" * n + b"\npartial")
            self.app.on_cava_stdout(r, m.GLib.IOCondition.IN)
            self.assertEqual(self.app.cava_shared_data, [.25] * n)
            self.assertEqual(self.app.cava_buffer, b"partial")
            self.app.cava_buffer = b""
            os.write(w, b"1;" * n + b"\n")
            self.app.on_cava_stdout(r, m.GLib.IOCondition.IN)
            self.assertFalse(any(self.app.cava_shared_data))
        finally:
            os.close(r)
            os.close(w)

    def test_audio_failure_clears_stale_targets_and_restarts(self):
        self.app.cava_shared_data = [.8] * self.app.config.bars
        self.app.cava_available = True
        r, w = os.pipe()
        try:
            self.assertFalse(self.app.on_cava_stdout(r, m.GLib.IOCondition.HUP))
            self.assertFalse(any(self.app.cava_shared_data))
            self.assertIsNotNone(self.app.cava_restart_source)
        finally:
            os.close(r)
            os.close(w)

    def test_idle_decay_finishes(self):
        self.app.config.idle_wave = False
        self.app.smoothed_data = [.9] * self.app.config.bars
        for _ in range(50):
            data = self.app.prepare_render_data()
        self.assertIsNone(data)
        self.assertTrue(self.app.has_rendered_idle_clear)

    def test_mirror_output(self):
        self.app.config.mirror = True
        self.app.config.smoothing = 0
        n = self.app.config.bars
        self.app.cava_shared_data = [i / n for i in range(n)]
        data = self.app.prepare_render_data()
        self.assertEqual(data[n // 2:], data[:n // 2][::-1])

    def test_invalid_config_retains_last_good_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            colors = Path(tmp) / "colors.json"
            with patch.object(m, "CONFIG_FILE", config), patch.object(m, "COLORS_FILE", colors):
                self.app.config.gain = 3
                config.write_text('{"gain":')
                self.app.apply_config_changes = lambda old: None
                self.app.execute_reload()
                self.assertEqual(self.app.config.gain, 3)
                config.write_bytes(b'\xff')
                self.app.execute_reload()
                self.assertEqual(self.app.config.gain, 3)

    def test_immediate_external_edit_is_not_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "visualizer.json"
            with patch.object(m, "CONFIG_FILE", path):
                self.app.save_config()
                queued = []
                self.app.queue_reload = lambda: queued.append(True)
                self.app.on_dir_changed(None, m.Gio.File.new_for_path(str(path)), None,
                    m.Gio.FileMonitorEvent.CHANGES_DONE_HINT)
                self.assertEqual(queued, [True])

    def test_instance_lock_keeps_inode_and_excludes_second_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "instance.lock"
            with patch.object(m, "LOCK_FILE", path):
                self.app.acquire_lock()
                ino = path.stat().st_ino
                result = subprocess.run([sys.executable, "-c",
                    "import os,fcntl,sys; f=os.open(sys.argv[1],os.O_RDWR); "
                    "fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)", str(path)],
                    capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.app.release_lock()
                self.assertEqual(path.stat().st_ino, ino)

    def test_cairo_every_style_position_and_bar_limit_is_visible(self):
        for n in (16, 72, 256):
            self.app.config.bars = n
            for style in m.Style:
                for position in m.Position:
                    with self.subTest(bars=n, style=style, position=position):
                        self.app.config.style = style
                        self.app.config.position = position
                        self.app.content_height = 100
                        surface = m.cairo.ImageSurface(m.cairo.FORMAT_ARGB32, 320, 180)
                        self.app.draw_cairo(m.cairo.Context(surface), 320, 180, [.4] * n)
                        self.assertTrue(any(surface.get_data()))

    def test_cairo_draw_restores_context_transform(self):
        class Widget:
            def get_allocated_width(self):
                return 320
            def get_allocated_height(self):
                return 180
        self.app.config.position = m.Position.BOTTOM
        self.app.content_height = 100
        surface = m.cairo.ImageSurface(m.cairo.FORMAT_ARGB32, 320, 180)
        context = m.cairo.Context(surface)
        before = tuple(context.get_matrix())
        self.app.on_draw(Widget(), context)
        self.assertEqual(tuple(context.get_matrix()), before)
        self.assertTrue(any(surface.get_data()))

    def test_atomic_deploy_normalizes_without_destroying_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "visualizer.json"
            with patch.object(m, "CONFIG_DIR", path.parent), patch.object(m, "CONFIG_FILE", path):
                path.write_text(json.dumps({"fps": 9999, "extension": "keep"}))
                m.deploy_config()
                data = json.loads(path.read_text())
                self.assertEqual(data["fps"], 240)
                self.assertEqual(data["extension"], "keep")
                path.write_text("broken")
                with self.assertRaises(ValueError):
                    m.deploy_config()
                self.assertEqual(path.read_text(), "broken")


class ClientTests(unittest.TestCase):
    def run_client(self, active, command, reader=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fifo = root / "dusky/settings/way_layers/visualizer/visualizer.ctl"
            fifo.parent.mkdir(parents=True)
            os.mkfifo(fifo)
            r = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK) if reader else None
            calls = []
            def systemctl(args, **kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0 if active or "is-active" not in args else 3)
            try:
                with patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_RUNTIME_DIR": tmp}), \
                     patch.object(sys, "argv", ["visualizer_ctl.py", command]), \
                     patch.object(ctl.subprocess, "run", side_effect=systemctl):
                    status = ctl.main()
                payload = os.read(r, 100) if r is not None else b""
                return status, payload, calls
            finally:
                if r is not None:
                    os.close(r)

    def test_active_toggle(self):
        status, payload, calls = self.run_client(True, "toggle")
        self.assertEqual((status, payload), (0, b"toggle\n"))
        self.assertEqual(len(calls), 1)

    def test_start_disabled_or_enabled_uses_idempotent_enable(self):
        status, payload, calls = self.run_client(False, "toggle")
        self.assertEqual((status, payload), (0, b"enable\n"))
        self.assertIn("start", calls[-1])

    def test_overlay_starts_without_changing_enabled(self):
        status, payload, _ = self.run_client(False, "overlay")
        self.assertEqual((status, payload), (0, b"overlay\n"))

    def test_stale_fifo_has_bounded_failure(self):
        with patch.object(ctl.time, "monotonic", side_effect=[0, 6]):
            status, payload, _ = self.run_client(True, "toggle", reader=False)
        self.assertEqual((status, payload), (1, b""))


if __name__ == "__main__":
    unittest.main()
