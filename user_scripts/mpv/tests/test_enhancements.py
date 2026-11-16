#!/usr/bin/env python3
"""Comprehensive IPC integration tests for shader_presets.lua, autoskip.lua, and waveform.lua."""

import json
import os
import pathlib
import socket
import subprocess
import tempfile
import time
import unittest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
ASSETS_DIR = TESTS_DIR.parent / "setup" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
WAVEFORM_PATH = SCRIPTS_DIR / "dusky_player" / "lib" / "waveform.lua"
SHADER_PRESETS_PATH = SCRIPTS_DIR / "shader_presets.lua"
AUTOSKIP_PATH = SCRIPTS_DIR / "autoskip.lua"


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

        # 1 -> 2: Fast (AMD CAS)
        client.command("script-binding", "shader_presets/cycle-preset")
        self.assertTrue(
            client.wait_for(
                lambda: client.get_prop("glsl-shaders") == ["~~/shaders/CAS-scaled.glsl"]
            )
        )

        # 2 -> 3: Live-Action (RAVU)
        client.command("script-binding", "shader_presets/cycle-preset")
        self.assertTrue(
            client.wait_for(
                lambda: client.get_prop("glsl-shaders")
                == ["~~/shaders/ravu-zoom-ar-r3.hook"]
            )
        )

        # 3 -> 4: Anime (FSRCNNX + SSimDownscaler)
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

        # 4 -> 1: Off (wrap around)
        client.command("script-binding", "shader_presets/cycle-preset")
        self.assertTrue(client.wait_for(lambda: client.get_prop("glsl-shaders") == []))

        # Cycle to CAS, then explicitly reset to Off
        client.command("script-binding", "shader_presets/cycle-preset")
        self.assertTrue(
            client.wait_for(
                lambda: client.get_prop("glsl-shaders") == ["~~/shaders/CAS-scaled.glsl"]
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


if __name__ == "__main__":
    unittest.main()
