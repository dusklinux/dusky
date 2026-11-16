#!/usr/bin/env python3
"""Comprehensive IPC integration tests for shader_presets.lua, autoskip.lua, and waveform.lua."""

import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import glob
for _site in sorted(glob.glob("/usr/lib/python3.*/site-packages"), reverse=True):
    if _site not in sys.path:
        sys.path.insert(0, _site)

TESTS_DIR = pathlib.Path(__file__).resolve().parent
ASSETS_DIR = TESTS_DIR.parent / "setup" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
WAVEFORM_PATH = SCRIPTS_DIR / "dusky_player" / "lib" / "waveform.lua"
SHADER_PRESETS_PATH = SCRIPTS_DIR / "shader_presets.lua"
AUTOSKIP_PATH = SCRIPTS_DIR / "autoskip.lua"
MOTION_TIER_PATH = SCRIPTS_DIR / "motion_tier.lua"
MOTION_VPY_PATH = ASSETS_DIR / "vapoursynth" / "motion_interpolate.vpy"


class MpvIpcClient:
    """Minimal JSON-IPC client for headless mpv testing."""

    def __init__(self, scripts, extra_args=None):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="mpv_test_")
        self.sock_path = os.path.join(self.tmpdir.name, "mpv.sock")
        cmd = [
            "mpv",
            "--no-config",
            "--idle=yes",
            "--vo=null",
            "--ao=null",
            "--no-terminal",
            f"--input-ipc-server={self.sock_path}",
        ]
        for script in scripts:
            cmd.append(f"--script={script}")
        if extra_args:
            cmd.extend(extra_args)

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._req_id = 0
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if os.path.exists(self.sock_path):
                break
            time.sleep(0.02)
        else:
            self.close()
            raise RuntimeError("Timed out waiting for mpv IPC socket")

    def command(self, *args):
        self._req_id += 1
        req_id = self._req_id
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(self.sock_path)
            payload = json.dumps({"command": list(args), "request_id": req_id}) + "\n"
            sock.sendall(payload.encode("utf-8"))
            stream = sock.makefile("r", encoding="utf-8")
            while True:
                line = stream.readline()
                if not line:
                    raise RuntimeError("EOF on mpv IPC socket")
                reply = json.loads(line)
                if reply.get("request_id") == req_id:
                    if (
                        args
                        and args[0] == "get_property"
                        and reply.get("error") in ("property unavailable", "property not found")
                    ):
                        return None
                    if reply.get("error") != "success":
                        raise AssertionError(f"IPC command {args} failed: {reply}")
                    return reply.get("data")

    def get_prop(self, name):
        return self.command("get_property", name)

    def set_prop(self, name, value):
        return self.command("set_property", name, value)

    def wait_for(self, predicate, timeout=5.0, interval=0.03):
        deadline = time.monotonic() + timeout
        last_val = None
        while time.monotonic() < deadline:
            last_val = predicate()
            if last_val:
                return last_val
            time.sleep(interval)
        return last_val

    def close(self):
        if self.proc.poll() is None:
            try:
                self.command("quit")
            except Exception:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.tmpdir.cleanup()


class ShaderPresetsTests(unittest.TestCase):
    """Verify shader_presets.lua cycling and reset behavior via mpv IPC."""

    def test_cycle_and_reset_shader_presets(self):
        client = MpvIpcClient([str(SHADER_PRESETS_PATH)])
        self.addCleanup(client.close)

        self.assertEqual(client.get_prop("glsl-shaders"), [])

        # 1 -> 2: Live-Action (RAVU)
        client.command("script-binding", "shader_presets/cycle-preset")
        self.assertTrue(
            client.wait_for(
                lambda: client.get_prop("glsl-shaders")
                == ["~~/shaders/ravu-zoom-ar-r3.hook"]
            )
        )

        # 2 -> 3: Anime (FSRCNNX + SSimDownscaler)
        client.command("script-binding", "shader_presets/cycle-preset")
        self.assertTrue(
            client.wait_for(
                lambda: client.get_prop("glsl-shaders")
                == [
                    "~~/shaders/FSRCNNX_x2_8-0-4-1.glsl",
                    "~~/shaders/SSimDownscaler.glsl",
                ]
            )
        )

        # 3 -> 1: Off (wrap around)
        client.command("script-binding", "shader_presets/cycle-preset")
        self.assertTrue(client.wait_for(lambda: client.get_prop("glsl-shaders") == []))

        # Cycle to RAVU, then explicitly reset to Off
        client.command("script-binding", "shader_presets/cycle-preset")
        self.assertTrue(
            client.wait_for(
                lambda: client.get_prop("glsl-shaders")
                == ["~~/shaders/ravu-zoom-ar-r3.hook"]
            )
        )
        client.command("script-binding", "shader_presets/reset-preset")
        self.assertTrue(client.wait_for(lambda: client.get_prop("glsl-shaders") == []))


class AutoskipTests(unittest.TestCase):
    """Verify autoskip.lua chapter skipping, chaining, false-positive guard, and Backspace undo."""

    @classmethod
    def setUpClass(cls):
        cls.media_dir = tempfile.TemporaryDirectory(prefix="mpv_autoskip_media_")
        cls.video_path = os.path.join(cls.media_dir.name, "sample.mkv")
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=12",
                "-c:a",
                "flac",
                cls.video_path,
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.media_dir.cleanup()

    def _load_with_chapters(self, client, chapters):
        meta_lines = [";FFMETADATA1\n"]
        for i, chap in enumerate(chapters):
            start_ms = int(chap["time"] * 1000)
            next_time = chapters[i + 1]["time"] if i + 1 < len(chapters) else 12.0
            end_ms = int(next_time * 1000)
            meta_lines.append(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start_ms}\nEND={end_ms}\ntitle={chap['title']}\n\n")

        ts = time.time_ns()
        meta_file = os.path.join(self.media_dir.name, f"meta_{ts}.txt")
        vid_file = os.path.join(self.media_dir.name, f"vid_{ts}.mkv")
        with open(meta_file, "w", encoding="utf-8") as f:
            f.writelines(meta_lines)

        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=12", "-i", meta_file, "-map_metadata", "1", "-c:v", "rawvideo", "-pix_fmt", "yuv420p", vid_file],
            check=True,
        )
        client.command("loadfile", vid_file, "replace")
        client.wait_for(lambda: client.get_prop("duration") is not None)
        client.set_prop("pause", True)
        client.command("seek", 0, "absolute+exact")
        time.sleep(0.1)

    def test_single_skip_and_undo_does_not_loop(self):
        client = MpvIpcClient([str(AUTOSKIP_PATH)])
        self.addCleanup(client.close)

        chapters = [
            {"title": "Prologue", "time": 0.0},
            {"title": "Intro", "time": 2.0},
            {"title": "Main Episode", "time": 6.0},
            {"title": "Epilogue", "time": 10.0},
        ]
        self._load_with_chapters(client, chapters)

        # Enable autoskip
        client.command("script-binding", "autoskip/toggle")
        time.sleep(0.1)

        # Seek into Chapter 1 ("Intro" at 2.0s) -> should auto-skip to Chapter 2 (6.0s)
        client.command("seek", 2.2, "absolute+exact")
        self.assertTrue(
            client.wait_for(lambda: client.get_prop("chapter") == 2),
            f"Expected chapter 2 after auto-skip, got {client.get_prop('chapter')}",
        )

        # Press Backspace (autoskip/undo) -> should return to ~2.2s (Chapter 1) and STAY there
        client.command("script-binding", "autoskip/undo")
        self.assertTrue(
            client.wait_for(lambda: client.get_prop("chapter") == 1),
            f"Expected undo to return to chapter 1, got {client.get_prop('chapter')}",
        )
        time.sleep(0.25)
        self.assertEqual(client.get_prop("chapter"), 1, "Undo must not re-trigger auto-skip!")

    def test_chained_skip_and_undo_returns_to_chain_origin(self):
        client = MpvIpcClient([str(AUTOSKIP_PATH)])
        self.addCleanup(client.close)

        chapters = [
            {"title": "Start", "time": 0.0},
            {"title": "Recap", "time": 2.0},
            {"title": "Opening 1", "time": 4.0},
            {"title": "Main Content", "time": 7.0},
        ]
        self._load_with_chapters(client, chapters)
        client.command("script-binding", "autoskip/toggle")
        time.sleep(0.1)

        # Enter Chapter 1 ("Recap") -> should chain through Chapter 2 ("Opening 1") -> land on Chapter 3 ("Main Content")
        client.command("seek", 2.1, "absolute+exact")
        self.assertTrue(
            client.wait_for(lambda: client.get_prop("chapter") == 3),
            f"Expected chained skip to reach chapter 3, got {client.get_prop('chapter')}",
        )

        # Undo should return to Chapter 1 ("Recap") and suppress both Recap and Opening 1
        client.command("script-binding", "autoskip/undo")
        self.assertTrue(
            client.wait_for(lambda: client.get_prop("chapter") == 1),
            f"Expected undo to return to chain start (chapter 1), got {client.get_prop('chapter')}",
        )
        time.sleep(0.25)
        self.assertEqual(client.get_prop("chapter"), 1)

    def test_story_titles_not_falsely_skipped_and_speed_fallback(self):
        client = MpvIpcClient([str(AUTOSKIP_PATH)])
        self.addCleanup(client.close)

        chapters = [
            {"title": "Start", "time": 0.0},
            {"title": "Recapturing Wall Maria", "time": 2.0},
            {"title": "Opening the Gate", "time": 5.0},
            {"title": "Never-Ending Rain", "time": 8.0},
        ]
        self._load_with_chapters(client, chapters)
        client.command("script-binding", "autoskip/toggle")
        time.sleep(0.1)

        for idx, sec in [(1, 2.2), (2, 5.2), (3, 8.2)]:
            client.command("seek", sec, "absolute+exact")
            time.sleep(0.2)
            self.assertEqual(
                client.get_prop("chapter"),
                idx,
                f"Chapter {idx} ('{chapters[idx]['title']}') should not be falsely skipped",
            )

        # Backspace fallback when no skip occurred resets playback speed to 1.0
        client.set_prop("speed", 1.75)
        client.command("script-binding", "autoskip/undo")
        self.assertTrue(client.wait_for(lambda: abs((client.get_prop("speed") or 0) - 1.0) < 1e-4))


class WaveformTests(unittest.TestCase):
    """Verify waveform.lua peak extraction, silent track handling, and narrow-window guards."""

    @classmethod
    def setUpClass(cls):
        cls.media_dir = tempfile.TemporaryDirectory(prefix="mpv_waveform_media_")
        cls.tone_path = os.path.join(cls.media_dir.name, "tone.flac")
        cls.silent_path = os.path.join(cls.media_dir.name, "silent.flac")

        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=4:sample_rate=48000",
                "-c:a",
                "flac",
                cls.tone_path,
            ],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=mono:d=4",
                "-c:a",
                "flac",
                cls.silent_path,
            ],
            check=True,
        )

        cls.harness_path = os.path.join(cls.media_dir.name, "waveform_harness.lua")
        with open(cls.harness_path, "w", encoding="utf-8") as f:
            f.write(
                f"""
mp = require('mp')
msg = require('mp.msg')
utils = require('mp.utils')

state = {{ path = nil }}
options = {{ timeline_waveform = true }}
config = {{ color = {{ heatmap = 'FFFFFF' }} }}
function normalize_path(p) return p end

local wf
local owner = {{
    register_disposer = function(_, fn) end,
    register_mp_event = function(_, ev, fn) mp.register_event(ev, fn) end,
    observe_mp_property = function(_, prop, fmt, fn) mp.observe_property(prop, fmt, fn) end,
}}

local function publish()
    if not wf then return end
    local ass_events = 0
    local narrow_events = 0
    local mock_ass = {{
        new_event = function() ass_events = ass_events + 1 end,
        append = function() end,
        opacity = function() end,
    }}
    local mock_narrow = {{
        new_event = function() narrow_events = narrow_events + 1 end,
        append = function() end,
        opacity = function() end,
    }}
    if wf.data then
        wf:render(mock_narrow, 10, 10, 10, 30, 1.0, 2, nil, nil)
        wf:render(mock_ass, 0, 0, 500, 30, 1.0, 2, 200, 202)
    end
    mp.set_property_native('user-data/test/waveform', {{
        has_data = wf.data ~= nil,
        bins = wf.data and #wf.data.points or 0,
        path_len = wf.path and #wf.path or -1,
        ass_events = ass_events,
        narrow_events = narrow_events,
        key = wf.key or '',
    }})
end

function request_render()
    publish()
end

local Waveform = dofile({json.dumps(str(WAVEFORM_PATH))})
wf = Waveform.new(owner)
"""
            )

    @classmethod
    def tearDownClass(cls):
        cls.media_dir.cleanup()

    def test_waveform_tone_silent_and_narrow_guard(self):
        client = MpvIpcClient([self.harness_path])
        self.addCleanup(client.close)

        # 1. Load normal sine-wave audio file
        client.command("loadfile", self.tone_path, "replace")
        res = client.wait_for(
            lambda: (
                (d := client.get_prop("user-data/test/waveform"))
                and d.get("has_data")
                and d
            )
        )
        self.assertIsNotNone(res)
        self.assertGreater(res["bins"], 0)
        self.assertGreater(res["path_len"], 0)
        self.assertEqual(res["ass_events"], 2)  # Left and right of progress line
        self.assertEqual(res["narrow_events"], 0)  # Narrow width < 1 guard

        # 2. Load completely silent audio file -> bins > 0, path == "", ass_events == 0
        client.command("loadfile", self.silent_path, "replace")
        res_silent = client.wait_for(
            lambda: (
                (d := client.get_prop("user-data/test/waveform"))
                and d.get("has_data")
                and self.silent_path in d.get("key", "")
                and d
            )
        )
        self.assertIsNotNone(res_silent)
        self.assertGreater(res_silent["bins"], 0)
        self.assertEqual(res_silent["path_len"], 0)
        self.assertEqual(res_silent["ass_events"], 0)


class TestMotionTierInterpolation(unittest.TestCase):
    """Comprehensive IPC & VapourSynth test suite for Two-Tier Motion Interpolation."""

    @classmethod
    def setUpClass(cls):
        cls.media_dir = tempfile.TemporaryDirectory(prefix="mpv_motion_test_")
        cls.media_path = pathlib.Path(cls.media_dir.name)

        cls.test_video = cls.media_path / "test_24fps.mp4"
        cls.clip_60fps = cls.media_path / "clip_60fps.mp4"
        cls.clip_odd = cls.media_path / "clip_641x359.mkv"
        cls.audio_only = cls.media_path / "audio_only.flac"

        cls._gen_video(cls.test_video, "320x240", 24, 4, "yuv420p")
        cls._gen_video(cls.clip_60fps, "320x240", 60, 4, "yuv420p")
        cls._gen_video(cls.clip_odd, "641x359", 24, 3, "yuv444p")

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=4",
                str(cls.audio_only),
            ],
            check=True,
        )

        cls.config_dir = cls.media_path / "mpv_config"
        (cls.config_dir / "scripts").mkdir(parents=True)
        (cls.config_dir / "vapoursynth").mkdir(parents=True)
        import shutil

        shutil.copy2(MOTION_TIER_PATH, cls.config_dir / "scripts" / "motion_tier.lua")
        shutil.copy2(MOTION_VPY_PATH, cls.config_dir / "vapoursynth" / "motion_interpolate.vpy")

    @staticmethod
    def _gen_video(out_path: pathlib.Path, size: str, rate: int, duration: int, pix_fmt: str) -> None:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={size}:rate={rate}:duration={duration}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                pix_fmt,
                "-preset",
                "ultrafast",
                str(out_path),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.media_dir.cleanup()

    def _start_client(self, media_path=None, display_fps=60, extra_args=None):
        target_media = media_path or self.test_video
        args = [
            f"--config-dir={self.config_dir}",
            "--video-sync=display-resample",
            f"--display-fps-override={display_fps}",
            "--interpolation=yes",
            "--tscale=oversample",
            "--hwdec=no",
            "--loop-file=inf",
        ]
        if extra_args:
            args.extend(extra_args)
        client = MpvIpcClient([str(self.config_dir / "scripts" / "motion_tier.lua")], extra_args=args)
        self.addCleanup(client.close)
        client.command("loadfile", str(target_media), "replace")
        client.wait_for(lambda: client.get_prop("duration") is not None)
        return client

    def test_tier1_default_and_toggle_states(self):
        client = self._start_client()
        mode = client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier1" and "tier1")
        self.assertEqual(mode, "tier1")
        self.assertTrue(client.get_prop("interpolation"))
        self.assertEqual(client.get_prop("video-sync"), "display-resample")
        self.assertEqual(client.get_prop("tscale"), "oversample")

        # Toggle off
        client.command("script-message-to", "motion_tier", "set-mode", "off")
        mode_off = client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "off" and "off")
        self.assertEqual(mode_off, "off")
        self.assertFalse(client.get_prop("interpolation"))

        # Revert back to Tier 1
        client.command("script-message-to", "motion_tier", "set-mode", "tier1")
        mode_t1 = client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier1" and "tier1")
        self.assertEqual(mode_t1, "tier1")
        self.assertTrue(client.get_prop("interpolation"))

    def test_tier2_mvtools_activation_and_hwdec_guard(self):
        client = self._start_client()
        client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier1")

        # Activate Tier 2 MVTools
        client.command("script-message-to", "motion_tier", "set-mode", "tier2_mvtools")
        mode_mvt = client.wait_for(
            lambda: client.get_prop("user-data/motion-tier/mode") == "tier2_mvtools" and "tier2_mvtools"
        )
        self.assertEqual(mode_mvt, "tier2_mvtools")

        hw = client.get_prop("hwdec")
        is_auto_copy = ("auto-copy" in hw) if isinstance(hw, list) else (hw == "auto-copy")
        self.assertTrue(is_auto_copy, f"Expected hwdec to be auto-copy, got {hw}")
        self.assertFalse(client.get_prop("interpolation"), "Tier 1 tscale must be disabled when Tier 2 is active")

        vf_list = client.get_prop("vf") or []
        labels = [f.get("label") for f in vf_list if isinstance(f, dict)]
        self.assertIn("motion_tier2", labels)

        # Revert back to Tier 1
        client.command("script-message-to", "motion_tier", "set-mode", "tier1")
        client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier1")
        vf_restored = client.get_prop("vf") or []
        self.assertEqual(len(vf_restored), 0)

    def test_tier2_frame_rate_multiplication(self):
        client = self._start_client()
        client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier1")

        # Set target 2x and activate tier2_auto (which uses MVTools fallback when RIFE is not loaded)
        client.command("script-message-to", "motion_tier", "set-target", "2x")
        client.command("script-message-to", "motion_tier", "set-mode", "tier2_auto")
        mode = client.wait_for(
            lambda: client.get_prop("user-data/motion-tier/mode") == "tier2_auto" and "tier2_auto"
        )
        self.assertEqual(mode, "tier2_auto")

        # Wait for frame multiplier pipeline to reach 48 FPS
        vf_fps = client.wait_for(
            lambda: (
                (val := client.get_prop("estimated-vf-fps"))
                and float(val) >= 44.0
                and float(val)
            ),
            timeout=10.0,
            interval=0.1,
        )
        self.assertTrue(vf_fps and vf_fps is not False, "Expected estimated-vf-fps to reach >= 44.0")
        self.assertAlmostEqual(vf_fps, 48.0, delta=4.0)

    def test_rapid_mode_switching_stress(self):
        """Rapidly cycle modes over IPC to verify zero filter leaking, deadlocks, or hwdec desync."""
        client = self._start_client()
        client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier1")

        # 16 rapid transitions between tier2 engines and tier1
        for _ in range(8):
            client.command("script-message-to", "motion_tier", "set-mode", "tier2_mvtools")
            client.command("script-message-to", "motion_tier", "set-mode", "tier2_auto")

        # End with unique 'off' to flush pipeline (never sent in above loop)
        client.command("script-message-to", "motion_tier", "set-mode", "off")
        self.assertTrue(
            client.wait_for(
                lambda: client.get_prop("user-data/motion-tier/mode") == "off"
                and len(client.get_prop("vf") or []) == 0
                and client.get_prop("interpolation") is False,
                timeout=8.0,
            ),
            "Failed to settle on 'off' mode after rapid switching",
        )
        self.assertFalse(client.get_prop("interpolation"))

        # Transition back to tier1 and verify clean state
        client.command("script-message-to", "motion_tier", "set-mode", "tier1")
        self.assertTrue(
            client.wait_for(
                lambda: client.get_prop("user-data/motion-tier/mode") == "tier1"
                and len(client.get_prop("vf") or []) == 0
                and client.get_prop("interpolation") is True,
                timeout=8.0,
            ),
            "Failed to cleanly return to tier1 after rapid switching",
        )

        # Switch back to tier2_mvtools and verify exactly 1 filter exists
        client.command("script-message-to", "motion_tier", "set-mode", "tier2_mvtools")
        self.assertTrue(
            client.wait_for(
                lambda: client.get_prop("user-data/motion-tier/mode") == "tier2_mvtools"
                and len(client.get_prop("vf") or []) == 1,
                timeout=8.0,
            ),
            "Failed to cleanly activate tier2_mvtools after rapid switching",
        )
        vf_tier2 = client.get_prop("vf") or []
        self.assertEqual(len(vf_tier2), 1)
        self.assertEqual(vf_tier2[0].get("label"), "motion_tier2")

    def test_resolution_edge_cases_odd_and_4k_downscale(self):
        """Verify odd dimensions (641x359, 1920x803) and 4K (3840x2160) downscaling in VapourSynth."""
        import vapoursynth as vs
        core = vs.core

        vpy_file = self.config_dir / "vapoursynth" / "motion_interpolate.vpy"
        code = vpy_file.read_text(encoding="utf-8")

        def run_vpy(w: int, h: int, src_fps: float, disp_fps: float, user_data: str):
            blank = core.std.BlankClip(
                width=w,
                height=h,
                format=vs.YUV420P8 if (w % 2 == 0 and h % 2 == 0) else vs.YUV444P8,
                length=24,
            )
            out_node = []

            def mock_set_output(node):
                out_node.append(node)

            orig_set_output = vs.VideoNode.set_output
            vs.VideoNode.set_output = mock_set_output
            try:
                env = {
                    "__builtins__": __builtins__,
                    "vs": vs,
                    "core": core,
                    "video_in": blank,
                    "container_fps": src_fps,
                    "display_fps": disp_fps,
                    "user_data": user_data,
                }
                exec(code, env)
            finally:
                vs.VideoNode.set_output = orig_set_output

            return out_node[0] if out_node else None

        # Case A: 641x359 odd dimensions -> normalized to even 640x358 and interpolated to 60fps
        out_odd1 = run_vpy(641, 359, 24.0, 60.0, "engine=mvtools,target=60")
        self.assertIsNotNone(out_odd1)
        self.assertEqual((out_odd1.width, out_odd1.height), (640, 358))
        self.assertAlmostEqual(float(out_odd1.fps), 60.0, delta=0.1)

        # Case B: 1920x803 cropped ultrawide -> normalized to 1920x802 without mod-32 border distortion
        out_odd2 = run_vpy(1920, 803, 24.0, 60.0, "engine=mvtools,target=60")
        self.assertIsNotNone(out_odd2)
        self.assertEqual((out_odd2.width, out_odd2.height), (1920, 802))

        # Case C: 4K 3840x2160 -> pre-downscaled to 1920x1080 (max_h=1080)
        out_4k = run_vpy(3840, 2160, 24.0, 60.0, "engine=mvtools,target=60,max_h=1080")
        self.assertIsNotNone(out_4k)
        self.assertEqual((out_4k.width, out_4k.height), (1920, 1080))
        self.assertAlmostEqual(float(out_4k.fps), 60.0, delta=0.1)

    def test_media_transition_video_to_audio_only(self):
        """Verify switching from video to audio-only while Tier 2 is active strips vf and avoids crashes."""
        client = self._start_client()
        client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier1")

        # Activate Tier 2 MVTools
        client.command("script-message-to", "motion_tier", "set-mode", "tier2_mvtools")
        client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier2_mvtools")

        # Load audio-only file over IPC
        client.command("loadfile", str(self.audio_only), "replace")
        client.wait_for(lambda: client.get_prop("filename") == self.audio_only.name)

        vf_audio = client.wait_for(lambda: len(client.get_prop("vf") or []) == 0 and "clean")
        self.assertEqual(vf_audio, "clean", "VapourSynth filter must be removed on audio-only files")
        self.assertFalse(client.get_prop("interpolation"))

    def test_target_cycling_and_property_publishing(self):
        """Verify cycle-target steps through display -> 2x -> 60 -> 120 -> display and updates user-data."""
        client = self._start_client()
        client.wait_for(lambda: client.get_prop("user-data/motion-tier/target") == "display")

        for expected_target in ["2x", "60", "120", "display"]:
            client.command("script-message-to", "motion_tier", "cycle-target")
            actual = client.wait_for(
                lambda: client.get_prop("user-data/motion-tier/target") == expected_target and expected_target
            )
            self.assertEqual(actual, expected_target)

    def test_native_high_fps_bypass(self):
        """Verify native 60fps media is bypassed (not doubled to 120fps) when target is 60Hz/display."""
        client = self._start_client(media_path=self.clip_60fps, display_fps=60)
        client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier1")

        client.command("script-message-to", "motion_tier", "set-target", "display")
        client.command("script-message-to", "motion_tier", "set-mode", "tier2_mvtools")
        client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier2_mvtools")

        time.sleep(0.5)
        # When bypassed, estimated-vf-fps is either None (unmodified passthrough) or ~60.0
        vf_fps = client.get_prop("estimated-vf-fps")
        if vf_fps is not None:
            self.assertAlmostEqual(
                float(vf_fps),
                60.0,
                delta=4.0,
                msg=f"Native 60fps clip must not be multiplied above ~60fps on 60Hz target, got {vf_fps}",
            )
        container_fps = client.get_prop("container-fps")
        self.assertAlmostEqual(float(container_fps), 60.0, delta=1.0)

    def test_10bit_and_rgb_and_single_frame_resilience(self):
        """Verify 10-bit YUV420P10 preservation, 8-bit RGB24 conversion, and 1-frame clip bypass."""
        import vapoursynth as vs
        core = vs.core
        vpy_file = self.config_dir / "vapoursynth" / "motion_interpolate.vpy"
        code = vpy_file.read_text(encoding="utf-8")

        def run_clip(clip, src_fps=24.0, disp_fps=60.0, user_data="engine=mvtools,target=60"):
            out_node = []
            orig_set_output = vs.VideoNode.set_output
            vs.VideoNode.set_output = lambda node: out_node.append(node)
            try:
                exec(
                    code,
                    {
                        "__builtins__": __builtins__,
                        "vs": vs,
                        "core": core,
                        "video_in": clip,
                        "container_fps": src_fps,
                        "display_fps": disp_fps,
                        "user_data": user_data,
                    },
                )
            finally:
                vs.VideoNode.set_output = orig_set_output
            return out_node[0] if out_node else None

        # 1. 10-bit YUV420P10 must preserve 10-bit depth
        clip_10b = core.std.BlankClip(width=640, height=360, format=vs.YUV420P10, length=24)
        out_10b = run_clip(clip_10b)
        self.assertIsNotNone(out_10b)
        self.assertEqual(out_10b.format.id, vs.YUV420P10)

        # 2. 8-bit RGB24 must convert cleanly to YUV420P8 without zimg matrix error
        clip_rgb = core.std.BlankClip(width=640, height=360, format=vs.RGB24, length=24)
        out_rgb = run_clip(clip_rgb)
        self.assertIsNotNone(out_rgb)
        self.assertEqual(out_rgb.format.id, vs.YUV420P8)

        # 3. Single-frame clip (length=1) must bypass without crashing MVTools
        clip_1f = core.std.BlankClip(width=640, height=360, format=vs.YUV420P8, length=1)
        out_1f = run_clip(clip_1f)
        self.assertIsNotNone(out_1f)
        self.assertEqual(out_1f.num_frames, 1)

    def test_hwdec_no_preservation(self):
        """Verify that starting mpv with --hwdec=no preserves hwdec=no in Tier 1 and Off modes."""
        client = self._start_client(extra_args=["--hwdec=no"])
        client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier1")
        hw1 = client.get_prop("hwdec")
        self.assertTrue("no" in hw1 if isinstance(hw1, list) else hw1 == "no")

        client.command("script-message-to", "motion_tier", "set-mode", "tier2_mvtools")
        client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier2_mvtools")
        hw_t2 = client.get_prop("hwdec")
        is_auto_copy = ("auto-copy" in hw_t2) if isinstance(hw_t2, list) else (hw_t2 == "auto-copy")
        self.assertTrue(is_auto_copy)

        client.command("script-message-to", "motion_tier", "set-mode", "tier1")
        client.wait_for(lambda: client.get_prop("user-data/motion-tier/mode") == "tier1")
        hw3 = client.get_prop("hwdec")
        self.assertTrue("no" in hw3 if isinstance(hw3, list) else hw3 == "no")


if __name__ == "__main__":
    unittest.main()
