"""Real FFmpeg integration tests plus output-failure / cancellation fixtures.

Run: python3 -m unittest discover -s user_scripts/media_converter/tests -v
"""

from fractions import Fraction
import hashlib
import http.server
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "dusky_converter.py"
sys.path.insert(0, str(ROOT))
from converter.core import atomic_output


def encode(*args):
    return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
                          capture_output=True, check=True)


def metadata(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)], capture_output=True, check=True)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class ConverterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_temp = tempfile.TemporaryDirectory(prefix="dusky-fixtures-")
        cls.fixtures = Path(cls.fixture_temp.name)
        cls.source = cls.fixtures / "source.mkv"
        subtitle = cls.fixtures / "captions.srt"
        subtitle.write_text("1\n00:00:00,000 --> 00:00:00,800\nCaption\n", encoding="utf-8")
        attachment = cls.fixtures / "attachment.txt"
        attachment.write_text("Not a MOV stream", encoding="utf-8")
        encode("-f", "lavfi", "-i", "testsrc2=size=128x72:rate=24:duration=1.2",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2",
               "-f", "lavfi", "-i", "sine=frequency=880:duration=1.2",
               "-i", subtitle, "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
               "-c:v", "libx264", "-c:a:0", "aac", "-c:a:1", "libopus", "-c:s", "srt",
               "-metadata:s:a:0", "title=Microphone", "-metadata:s:a:1", "title=Game",
               "-attach", attachment, "-metadata:s:t", "mimetype=text/plain", cls.source)
        cls.silent = cls.fixtures / "silent.mp4"
        encode("-f", "lavfi", "-i", "testsrc2=size=128x72:rate=24:duration=1", "-c:v", "libx264", cls.silent)
        cls.odd = cls.fixtures / "odd.mkv"
        encode("-f", "lavfi", "-i", "testsrc=size=127x71:rate=24:duration=1", "-c:v", "ffv1", cls.odd)
        cls.hevc = cls.fixtures / "hevc.mkv"
        encode("-i", cls.silent, "-c:v", "libx265", "-x265-params", "pools=1:frame-threads=1:log-level=error", cls.hevc)
        cls.anamorphic = cls.fixtures / "anamorphic.mp4"
        encode("-i", cls.silent, "-vf", "setsar=2", "-c:v", "libx264", cls.anamorphic)
        cls.vfr = cls.fixtures / "vfr.mkv"
        encode("-f", "lavfi", "-i", "testsrc2=size=128x72:rate=30:duration=1.2",
               "-vf", "select='not(mod(n,2))+not(mod(n,5))'", "-fps_mode", "vfr", "-c:v", "libx264", cls.vfr)

    @classmethod
    def tearDownClass(cls):
        cls.fixture_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dusky-test-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def run_cli(self, *args, success=True, env=None):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=self.root,
                                capture_output=True, text=True, timeout=30, env=env)
        if directory := os.environ.get("DUSKY_TEST_LOG_DIR"):
            logs = Path(directory)
            logs.mkdir(parents=True, exist_ok=True)
            log = logs / f"{self.id().split('.')[-1]}-{time.time_ns()}.json"
            log.write_text(json.dumps({"argv": [str(a) for a in args], "expected_success": success,
                                       "returncode": result.returncode, "stdout": result.stdout,
                                       "stderr": result.stderr}, ensure_ascii=False, indent=2), encoding="utf-8")
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def output(self, feature, suffix, source=None, *extra):
        path = self.root / (feature + suffix)
        self.run_cli(feature, source or self.source, "-o", path, *extra)
        return path, metadata(path)

    def test_help_and_argument_errors(self):
        self.run_cli("--help")
        for feature in ("gif", "video", "premiere", "audio", "info"):
            self.run_cli(feature, "--help")
        for option, value in (("--fps", "nan"), ("--fps", "0"), ("--width", "1"),
                              ("--start", "-1"), ("--duration", "inf")):
            self.run_cli("gif", self.source, option, value, success=False)
        self.run_cli("video", self.source, "--crf", "52", success=False)
        self.run_cli("audio", self.source, "--track", "-1", success=False)
        self.run_cli(success=False)
        self.run_cli("gif", success=False)

    def test_gif_presets_and_interpolation(self):
        for preset in ("web", "balanced", "quality"):
            target = self.root / f"{preset}.gif"
            self.run_cli("gif", self.source, "--preset", preset, "-o", target)
            result = metadata(target)
            self.assertEqual(result["streams"][0]["width"], 128)
            self.assertGreater(float(result["format"]["duration"]), 1)
        _, result = self.output("gif", ".gif", self.source, "--interpolate", "--fps", "30")
        self.assertGreaterEqual(float(result["format"]["duration"]), 1.15)

    def test_gif_trim_and_downscale(self):
        _, result = self.output("gif", ".gif", self.source, "--width", "64", "--start", "0.2", "--duration", "0.5")
        self.assertEqual(result["streams"][0]["width"], 64)
        self.assertAlmostEqual(float(result["format"]["duration"]), 0.5, delta=0.08)

    def test_gif_keeps_native_odd_dimensions(self):
        _, result = self.output("gif", ".gif", self.odd)
        self.assertEqual((result["streams"][0]["width"], result["streams"][0]["height"]), (127, 71))

    def test_single_frame_interpolation(self):
        source = self.root / "one.mkv"
        encode("-i", self.silent, "-frames:v", "1", "-c:v", "ffv1", source)
        self.output("gif", ".gif", source, "--interpolate")

    def test_remux_preserves_tracks_and_video_packets(self):
        target, result = self.output("premiere", ".mov")
        self.assertEqual([s["codec_name"] for s in result["streams"]], ["h264", "aac", "pcm_s24le"])
        self.assertNotIn("Multiple -codec", self.run_cli("premiere", self.source, "-o", target, "--overwrite").stderr)
        hashes = []
        for source in (self.source, target):
            packet = subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-map", "0:v:0",
                                     "-c", "copy", "-f", "h264", "-"], capture_output=True, check=True)
            hashes.append(hashlib.sha256(packet.stdout).hexdigest())
        self.assertEqual(*hashes)
        data = target.read_bytes()
        self.assertLess(data.index(b"moov"), data.index(b"mdat"))

    def test_hevc_tag(self):
        _, result = self.output("premiere", ".mov", self.hevc)
        self.assertEqual(result["streams"][0]["codec_tag_string"], "hvc1")

    def test_prores_source_fps_and_explicit_fps(self):
        _, result = self.output("premiere", ".mov", self.source, "--mode", "prores")
        self.assertEqual(result["streams"][0]["codec_name"], "prores")
        self.assertEqual(result["streams"][0]["pix_fmt"], "yuv422p10le")
        self.assertEqual(Fraction(result["streams"][0]["avg_frame_rate"]), 24)
        self.assertEqual([s["codec_name"] for s in result["streams"][1:]], ["pcm_s24le"] * 2)
        path = self.root / "cfr.mov"
        self.run_cli("premiere", self.vfr, "--mode", "prores", "--fps", "30", "-o", path)
        self.assertEqual(Fraction(metadata(path)["streams"][0]["avg_frame_rate"]), 30)

    def test_auto_prores_for_unsupported_codec_and_odd_dimensions(self):
        _, result = self.output("premiere", ".mov", self.odd)
        self.assertEqual(result["streams"][0]["codec_name"], "prores")
        self.assertEqual(result["streams"][0]["width"] % 2, 0)
        self.run_cli("premiere", self.odd, "--mode", "remux", success=False)

    def test_remux_rejects_timing_changes(self):
        self.run_cli("premiere", self.source, "--fps", "60", success=False)
        self.run_cli("premiere", self.source, "--duration", "0.5", success=False)

    def test_no_audio(self):
        for feature, suffix in (("premiere", ".mov"), ("video", ".mp4"), ("gif", ".gif")):
            _, result = self.output(feature, suffix, self.silent)
            self.assertEqual(len(result["streams"]), 1)
        self.run_cli("audio", self.silent, success=False)

    def test_video_presets_and_cfr_trim(self):
        for preset in ("fast", "balanced", "quality"):
            target = self.root / f"{preset}.mp4"
            self.run_cli("video", self.source, "--preset", preset, "--crf", "20", "-o", target)
            result = metadata(target)
            self.assertEqual([s["codec_name"] for s in result["streams"]], ["h264", "aac", "aac"])
        target = self.root / "trimmed.mp4"
        self.run_cli("video", self.source, "--width", "64", "--start", "0.2", "--duration", "0.5", "--fps", "30", "-o", target)
        result = metadata(target)
        self.assertEqual(result["streams"][0]["width"], 64)
        self.assertEqual(Fraction(result["streams"][0]["avg_frame_rate"]), 30)
        self.assertAlmostEqual(float(result["format"]["duration"]), 0.5, delta=0.1)

    def test_anamorphic_and_odd_mp4(self):
        _, result = self.output("video", ".mp4", self.anamorphic)
        self.assertEqual(result["streams"][0]["sample_aspect_ratio"], "1:1")
        self.assertAlmostEqual(float(Fraction(result["streams"][0]["display_aspect_ratio"].replace(":", "/"))), 32 / 9, delta=0.02)
        target = self.root / "odd.mp4"
        self.run_cli("video", self.odd, "-o", target)
        self.assertEqual(metadata(target)["streams"][0]["width"] % 2, 0)

    def test_portrait_and_vfr_preservation(self):
        portrait = self.root / "portrait.mp4"
        encode("-f", "lavfi", "-i", "testsrc2=size=72x128:rate=24:duration=1", "-c:v", "libx264", portrait)
        _, result = self.output("video", ".mp4", portrait, "--width", "36")
        self.assertEqual((result["streams"][0]["width"], result["streams"][0]["height"]), (36, 64))
        target = self.root / "vfr.mp4"
        self.run_cli("video", self.vfr, "-o", target)
        timestamps = []
        for source in (self.vfr, target):
            frames = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
                                     "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(source)],
                                    capture_output=True, check=True)
            timestamps.append([float(f["best_effort_timestamp_time"]) for f in json.loads(frames.stdout)["frames"]])
        self.assertEqual(len(timestamps[0]), len(timestamps[1]))
        for before, after in zip(*timestamps, strict=True):
            self.assertAlmostEqual(before, after, delta=0.002)
        self.assertGreater(len({round(b - a, 3) for a, b in zip(timestamps[1], timestamps[1][1:])}), 1)

    def test_missing_tools(self):
        directory = self.root / "empty-path"
        directory.mkdir()
        result = self.run_cli("video", self.source, success=False,
                              env=dict(os.environ, PATH=str(directory)))
        self.assertIn("Missing ffprobe", result.stderr)

    def test_transport_stream_trimming_keeps_video(self):
        source = self.root / "offset.ts"
        encode("-i", self.source, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", "aac", "-f", "mpegts", source)
        for feature, extension, extra, expected in (
                ("video", ".mp4", [], ["h264", "aac"]),
                ("gif", ".gif", [], ["gif"]),
                ("gif", ".gif", ["--interpolate"], ["gif"]),
                ("premiere", ".mov", ["--mode", "prores"], ["prores", "pcm_s24le"]),
                ("audio", ".flac", [], ["flac"])):
            target = self.root / f"ts-{feature}-{len(extra)}{extension}"
            result = self.run_cli(feature, source, "-o", target, "--start", "0.2", "--duration", "0.5", *extra)
            data = metadata(target)
            self.assertEqual([s["codec_name"] for s in data["streams"]], expected)
            self.assertAlmostEqual(float(data["format"]["duration"]), 0.5, delta=0.05)
            self.assertNotIn("No filtered frames", result.stderr)

    def test_corrupt_packets_preserve_existing_output(self):
        source = self.root / "corrupt.mp4"
        shutil.copyfile(self.silent, source)
        data = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                          "-show_packets", "-show_entries", "packet=pos,size", "-of", "json", str(source)],
                                         capture_output=True, check=True).stdout)
        packet = data["packets"][5]
        with source.open("r+b") as file:
            file.seek(int(packet["pos"]))
            file.write(b"\xff" * int(packet["size"]))
        target = self.root / "protected.mp4"
        target.write_bytes(b"original")
        self.run_cli("video", source, "-o", target, "--overwrite", success=False)
        self.assertEqual(target.read_bytes(), b"original")
        self.assertFalse(list(self.root.glob(".dusky-*")))

    def test_empty_selected_stream_is_failure(self):
        source = self.root / "delayed-audio.mp4"
        encode("-i", self.silent, "-itsoffset", "2", "-f", "lavfi", "-i", "sine=duration=0.7",
               "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", source)
        target = self.root / "protected.mp4"
        target.write_bytes(b"original")
        # The selected audio begins after this interval; silently dropping it is a failure.
        self.run_cli("video", source, "-o", target, "--duration", "0.5", "--overwrite", success=False)
        self.assertEqual(target.read_bytes(), b"original")

    def test_duration_caps_delayed_audio_timeline(self):
        source = self.root / "delayed-audio.mp4"
        encode("-i", self.silent, "-itsoffset", "0.2", "-f", "lavfi", "-i", "sine=duration=0.7",
               "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", source)
        target = self.root / "clipped.mp4"
        self.run_cli("video", source, "-o", target, "--duration", "0.5")
        data = metadata(target)
        self.assertEqual([s["codec_name"] for s in data["streams"]], ["h264", "aac"])
        self.assertAlmostEqual(float(data["format"]["duration"]), 0.5, delta=0.05)

    def test_audio_bit_depth_and_surround(self):
        mono = self.root / "mono.wav"
        encode("-f", "lavfi", "-i", "sine=duration=0.7", "-c:a", "pcm_s16le", mono)
        _, data = self.output("audio", ".flac", mono)
        self.assertEqual(data["streams"][0]["bits_per_raw_sample"], "16")
        surround = self.root / "surround.wav"
        encode("-f", "lavfi", "-i", "anullsrc=r=48000:cl=5.1", "-t", "0.7", "-c:a", "pcm_s24le", surround)
        _, data = self.output("audio", ".mp3", surround, "--format", "mp3")
        self.assertEqual(data["streams"][0]["channels"], 2)

    def test_cover_art_is_not_selected_as_video(self):
        cover = self.root / "cover.png"
        encode("-f", "lavfi", "-i", "color=c=red:size=64x64", "-frames:v", "1", "-update", "1", cover)
        source = self.root / "covered.mp4"
        encode("-i", self.silent, "-i", cover, "-map", "1:v", "-map", "0:v", "-c", "copy", "-disposition:v:0", "attached_pic", source)
        _, data = self.output("video", ".mp4", source)
        self.assertEqual(data["streams"][0]["width"], 128)
        self.assertEqual(data["streams"][0]["height"], 72)

    def test_audio_formats_and_track_selection(self):
        for format, codec in (("flac", "flac"), ("wav", "pcm_s24le"), ("mp3", "mp3"), ("m4a", "aac")):
            target = self.root / f"audio.{format}"
            result = self.run_cli("audio", self.source, "--format", format, "--track", "1", "-o", target)
            self.assertNotIn("experimental", result.stderr)
            streams = metadata(target)["streams"]
            self.assertEqual(len(streams), 1)
            self.assertEqual(streams[0]["codec_name"], codec)
        self.run_cli("audio", self.source, "--track", "2", success=False)

    def test_json_inspection(self):
        result = self.run_cli("info", self.source, "--json")
        data = json.loads(result.stdout)
        self.assertEqual(data[0]["path"], str(self.source))
        self.assertEqual(len(data[0]["streams"]), 5)
        self.assertNotIn("\x1b", result.stderr + result.stdout)

    def test_paths_batch_dedup_and_failure_exit(self):
        source = self.root / "-quoted ' [α].MKV"
        shutil.copyfile(self.source, source)
        outputs = self.root / "outputs with spaces"
        self.run_cli("video", "--output-dir", outputs, "--", source, source)
        self.assertEqual(len(list(outputs.glob("*.mp4"))), 1)
        bad = self.root / "broken.mkv"
        bad.write_bytes(b"not media")
        outputs2 = self.root / "batch"
        result = self.run_cli("video", self.root, "--output-dir", outputs2, success=False)
        self.assertIn("1 succeeded · 1 failed", result.stderr)
        self.assertTrue((outputs2 / (source.stem + "_compressed.mp4")).is_file())

    def test_existing_output_source_protection_and_collisions(self):
        target = self.root / "existing.mp4"
        target.write_bytes(b"original")
        self.run_cli("video", self.source, "-o", target, success=False)
        self.assertEqual(target.read_bytes(), b"original")
        self.run_cli("video", self.source, "-o", target, "--overwrite")
        self.run_cli("video", self.silent, "-o", self.silent, "--overwrite", success=False)
        self.run_cli("gif", self.source, "-o", self.root / "wrong.mp4", success=False)
        second = self.root / "source.mp4"
        shutil.copyfile(self.silent, second)
        self.run_cli("gif", self.source, second, "--output-dir", self.root, success=False)
        self.assertFalse((self.root / "source.gif").exists())
        self.run_cli("video", self.source, self.silent, "-o", target, success=False)

    def test_start_past_end(self):
        self.run_cli("gif", self.source, "--start", "999", "-o", self.root / "past.gif", success=False)
        self.assertFalse(list(self.root.glob(".dusky-*")))

    def test_atomic_publication_race(self):
        target = self.root / "race.mp4"
        with self.assertRaises(FileExistsError):
            with atomic_output(target, self.source, False) as pending:
                pending.write_bytes(b"new")
                target.write_bytes(b"another process")
        self.assertEqual(target.read_bytes(), b"another process")
        self.assertFalse(list(self.root.glob(".dusky-*")))

    def test_directory_batch_can_be_repeated(self):
        source = self.root / "batch-source"
        source.mkdir()
        shutil.copyfile(self.source, source / "clip.mkv")
        for feature, suffix, folder in (("video", "_compressed.mp4", "converted_video"),
                                         ("gif", ".gif", "gifs"),
                                         ("audio", "_audio1.flac", "extracted_audio")):
            self.run_cli(feature, source)
            self.run_cli(feature, source, "--overwrite")
            files = list((source / folder).glob("*"))
            self.assertEqual([p.name for p in files], ["clip" + suffix])

    def test_closed_report_pipe(self):
        for arguments in (("info", str(self.source), "--json"), ("--help",)):
            with subprocess.Popen([sys.executable, str(CLI), *arguments],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                process.stdout.close()
                errors = process.stderr.read()
                process.wait(timeout=10)
                self.assertEqual(process.returncode, 141, errors.decode())
                self.assertNotIn(b"Exception ignored", errors)
                self.assertNotIn(b"Traceback", errors)

    def test_concurrent_publication(self):
        target = self.root / "concurrent.mp4"
        processes = [subprocess.Popen([sys.executable, str(CLI), "video", str(self.source), "-o", str(target)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        try:
            results = [p.communicate(timeout=30) for p in processes]
            self.assertEqual(sum(p.returncode == 0 for p in processes), 1, results)
            self.assertEqual(metadata(target)["streams"][0]["codec_name"], "h264")
            self.assertFalse(list(self.root.glob(".dusky-*")))
            for _, errors in results:
                self.assertNotIn(b"Traceback", errors)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait()
                process.stdout.close()
                process.stderr.close()

    @unittest.skipUnless(os.environ.get("DUSKY_TEST_EXFAT_DIR"), "Set DUSKY_TEST_EXFAT_DIR to a test exFAT mount")
    def test_exfat_publication(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DUSKY_TEST_EXFAT_DIR"]) as directory:
            target = Path(directory) / "converted.mp4"
            self.run_cli("video", self.source, "-o", target)
            self.run_cli("video", self.source, "-o", target, "--overwrite")
            original = target.read_bytes()
            self.run_cli("video", self.source, "-o", target, success=False)
            self.assertEqual(original, target.read_bytes())
            self.assertEqual(metadata(target)["streams"][0]["codec_name"], "h264")
            with self.assertRaises(FileExistsError):
                with atomic_output(target.with_name("race.mp4"), self.source, False) as pending:
                    pending.write_bytes(b"new")
                    target.with_name("race.mp4").write_bytes(b"racing writer")
            self.assertEqual(target.with_name("race.mp4").read_bytes(), b"racing writer")

    def fake_tool(self, body):
        directory = self.root / "bin"
        directory.mkdir()
        tool = directory / "ffmpeg"
        tool.write_text(f"#!{sys.executable}\nimport sys, time, os, subprocess\nfrom pathlib import Path\n{body}\n", encoding="utf-8")
        tool.chmod(0o755)
        return dict(os.environ, PATH=str(directory) + os.pathsep + os.environ["PATH"])

    def test_partial_failure_preserves_existing_output(self):
        target = self.root / "protected.mp4"
        target.write_bytes(b"original")
        env = self.fake_tool("Path(sys.argv[-1]).write_bytes(b'partial')\nprint('fixture encoder failure', file=sys.stderr)\nraise SystemExit(1)")
        result = self.run_cli("video", self.source, "-o", target, "--overwrite", success=False, env=env)
        self.assertIn("fixture encoder failure", result.stderr)
        self.assertEqual(target.read_bytes(), b"original")
        self.assertFalse(list(self.root.glob(".dusky-*")))

    def test_large_encoder_log_does_not_deadlock(self):
        env = self.fake_tool("Path(sys.argv[-1]).write_bytes(b'partial')\nsys.stderr.write('x' * 2_000_000 + '\\nfixture tail\\n')\nraise SystemExit(1)")
        result = self.run_cli("video", self.source, "-o", self.root / "large-log.mp4", success=False, env=env)
        self.assertIn("fixture tail", result.stderr)
        self.assertLess(len(result.stderr), 10000)
        self.assertFalse((self.root / "large-log.mp4").exists())
        self.assertFalse(list(self.root.glob(".dusky-*")))

    def test_cancel_kills_term_ignoring_helper(self):
        marker = self.root / "stubborn.pid"
        child_code = ("import os,signal,time; from pathlib import Path; "
                      "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                      f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)")
        env = self.fake_tool(f"Path(sys.argv[-1]).write_bytes(b'partial')\nsubprocess.Popen([sys.executable, '-c', {child_code!r}])\ntime.sleep(60)")
        target = self.root / "stubborn.mp4"
        target.write_bytes(b"original")
        with subprocess.Popen([sys.executable, str(CLI), "video", str(self.source), "-o", str(target), "--overwrite"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as process:
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists())
                process.send_signal(signal.SIGINT)
                _, errors = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 130, errors.decode())
                child = Path(f"/proc/{marker.read_text()}/stat")
                deadline = time.monotonic() + 2
                while child.exists() and child.read_text().split()[2] != "Z" and time.monotonic() < deadline:
                    time.sleep(0.01)
                if child.exists():
                    self.assertEqual(child.read_text().split()[2], "Z")
            finally:
                if marker.exists():
                    try:
                        os.kill(int(marker.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.kill()
        self.assertEqual(target.read_bytes(), b"original")
        self.assertFalse(list(self.root.glob(".dusky-*")))

    def test_real_encoder_cancellation(self):
        source = self.root / "long.mp4"
        encode("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=4", "-c:v", "libx264", "-preset", "ultrafast", source)
        target = self.root / "cancelled.gif"
        target.write_bytes(b"original")
        log = self.root / "cancel.log"
        with log.open("wb") as errors:
            with subprocess.Popen([sys.executable, str(CLI), "gif", str(source), "-o", str(target),
                                   "--interpolate", "--fps", "60", "--overwrite"], stdout=subprocess.PIPE, stderr=errors) as process:
                try:
                    deadline = time.monotonic() + 10
                    while b"Palette" not in log.read_bytes() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertIn(b"Palette", log.read_bytes())
                    time.sleep(0.1)
                    process.send_signal(signal.SIGINT)
                    process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 130, log.read_text())
                finally:
                    if process.poll() is None:
                        process.kill()
        self.assertEqual(target.read_bytes(), b"original")
        self.assertFalse(list(self.root.glob(".dusky-*")))

    def test_cancel_reaps_process_group_and_cleans_partial(self):
        target = self.root / "protected.mp4"
        target.write_bytes(b"original")
        marker = self.root / "child.pid"
        env = self.fake_tool(
            "Path(sys.argv[-1]).write_bytes(b'partial')\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            f"Path({str(marker)!r}).write_text(str(child.pid))\n"
            "time.sleep(30)"
        )
        with subprocess.Popen([sys.executable, str(CLI), "video", str(self.source), "-o", str(target), "--overwrite"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as process:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists())
            process.send_signal(signal.SIGTERM)
            _, errors = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 143, errors.decode())
        self.assertEqual(target.read_bytes(), b"original")
        self.assertFalse(list(self.root.glob(".dusky-*")))
        child = Path(f"/proc/{marker.read_text()}/stat")
        # A killed orphan may remain briefly as a zombie until the host init reaps it.
        if child.exists():
            self.assertEqual(child.read_text().split()[2], "Z")

    def test_interactive_menu_and_progress(self):
        master, slave = pty.openpty()
        source = self.root / "interactive.mkv"
        shutil.copyfile(self.source, source)
        with subprocess.Popen([sys.executable, str(CLI)], stdin=slave, stdout=slave, stderr=slave,
                              cwd=self.root, env=dict(os.environ, TERM="xterm-256color")) as process:
            os.close(slave)
            os.write(master, f"1\n{source}\n".encode())
            data = bytearray()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        data.extend(os.read(master, 65536))
                    except OSError:
                        break
                if process.poll() is not None:
                    break
            process.wait(timeout=5)
            os.close(master)
            self.assertEqual(process.returncode, 0, data.decode(errors="replace"))
        output = source.with_suffix(".gif")
        self.assertTrue(output.is_file())
        output.unlink()
        self.assertIn(b"DUSKY CONVERTER", data)
        self.assertIn(b"\x1b[", data)

    @unittest.skipUnless(shutil.which("yt-dlp"), "yt-dlp required")
    def test_url_download_real_local_http(self):
        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(ConverterTests.fixtures), **kwargs)

            def log_message(self, *_):
                pass

        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/silent.mp4"
                self.run_cli("gif", url, "-o", self.root / "download.gif")
                self.assertTrue((self.root / "download.gif").is_file())
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
