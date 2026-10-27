"""Offline regressions: real yt-dlp/FFmpeg, tiny HTTP fixtures, isolated state.

Run with the interpreter containing the downloader's dependencies:
    /usr/bin/python -m unittest discover -s user_scripts/tools/yt_dlp_downloader -v
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import dusky_yt_dlp as app
from yt_dlp import YoutubeDL, parse_options


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class DownloaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory(prefix="dusky-media-", dir="/dev/shm")
        cls.media = Path(cls.fixtures.name)
        codecs = {"h264": ("libx264", "aac", "mp4"),
                  "av1": ("libaom-av1", "libopus", "mkv"),
                  "vp9": ("libvpx-vp9", "libopus", "webm")}
        for name, (video, audio, ext) in codecs.items():
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                       "-i", "color=c=blue:s=160x96:r=5:d=0.4", "-f", "lavfi",
                       "-i", "sine=frequency=440:duration=0.4", "-c:v", video,
                       "-threads", "1", "-c:a", audio, "-shortest", str(cls.media / f"{name}.{ext}")]
            subprocess.run(command, check=True, capture_output=True, timeout=30)
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(cls.media / "h264.mp4"),
                        "-vn", "-c:a", "copy", str(cls.media / "audio.m4a")], check=True, timeout=15)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=32x32",
                        "-frames:v", "1", "-threads", "1", str(cls.media / "cover.jpg")], check=True, timeout=15)
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(cls.media / "audio.m4a"),
                        "-i", str(cls.media / "cover.jpg"), "-map", "0:a", "-map", "1:v",
                        "-c:a", "libmp3lame", "-c:v", "copy", "-disposition:v", "attached_pic",
                        str(cls.media / "cover.mp3")], check=True, timeout=15)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(cls.media)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.fixtures.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dusky-test-", dir="/dev/shm")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "locks").mkdir()
        self.destination = self.root / "output % literal"
        self.destination.mkdir()

    def job(self, mode, filename="h264.mp4", height=None):
        return app.Job(filename, f"{self.base_url}/{filename}", mode, height)

    def execute(self, job, move_to=None):
        with app.progress_display() as progress:
            task = progress.add_task("", title="test", metric="")
            controller = app.QueueController([app.Slot(1, task)])
            slot = controller.claim(job.title)
            return app.execute_job(job, slot, controller, progress, self.destination,
                                   self.state, app.CookieChoice(), move_to=move_to, min_free_gb=0)

    def choose(self, mode, formats, height=None):
        with YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            normalized = [dict(url=f"{self.base_url}/h264.mp4", ext="mp4", format_id=str(i), **fmt)
                          for i, fmt in enumerate(formats)]
            return list(ydl.build_format_selector(app.format_arguments(mode, height)[1])({
                "formats": normalized, "incomplete_formats": all(
                    f.get("vcodec") == "none" or f.get("acodec") == "none" for f in normalized),
                "has_merged_format": any(f.get("vcodec") != "none" and f.get("acodec") != "none"
                                         for f in normalized)}))

    def test_audio_uses_best_combined_fallback(self):
        formats = [dict(vcodec="h264", acodec="aac", height=360),
                   dict(vcodec="h264", acodec="aac", height=1080)]
        for mode in app.TargetFormat:
            if not mode.is_video:
                with self.subTest(mode=mode):
                    self.assertEqual(self.choose(mode, formats)[0]["format_id"], "1")

    def test_best_video_considers_combined_formats(self):
        formats = [dict(vcodec="h264", acodec="none", height=360),
                   dict(vcodec="none", acodec="aac"),
                   dict(vcodec="h264", acodec="aac", height=1080)]
        for mode in (app.TargetFormat.VIDEO_BEST, app.TargetFormat.VIDEO_MKV):
            with self.subTest(mode=mode):
                self.assertEqual(self.choose(mode, formats)[0]["format_id"], "2")

    def test_codec_aliases_and_caps(self):
        for mode, codecs in ((app.TargetFormat.VIDEO_AV1, ("av1", "av01.0.08M.08")),
                             (app.TargetFormat.VIDEO_VP9, ("vp9", "vp09.00.10.08"))):
            for codec in codecs:
                with self.subTest(mode=mode, codec=codec):
                    formats = [dict(vcodec=codec, acodec="none", height=720),
                               dict(vcodec=codec, acodec="none", height=1080),
                               dict(vcodec="none", acodec="opus")]
                    result = self.choose(mode, formats, 720)[0]
                    self.assertEqual(result["requested_formats"][0]["height"], 720)
                    self.assertEqual(result["requested_formats"][1]["acodec"], "opus")
                    self.assertEqual(self.choose(mode, formats, 360), [])

    def test_strict_codecs_do_not_fall_back(self):
        formats = [dict(vcodec="h264", acodec="aac", height=720)]
        self.assertEqual(self.choose(app.TargetFormat.VIDEO_AV1, formats), [])
        self.assertEqual(self.choose(app.TargetFormat.VIDEO_VP9, formats), [])
        self.assertEqual(self.choose(app.TargetFormat.AUDIO_BEST,
                                     [dict(vcodec="h264", acodec="none", height=720)]), [])

    def test_command_options_are_valid(self):
        templates = set()
        for mode in app.TargetFormat:
            with self.subTest(mode=mode):
                args = app.ytdlp_command(self.job(mode), self.destination, self.state,
                                         self.root / "archive", [])
                parsed = parse_options(args[1:])
                self.assertFalse(parsed.ydl_opts["simulate"])
                self.assertFalse(parsed.ydl_opts["skip_unavailable_fragments"])
                template = args[args.index("-o") + 1]
                self.assertNotIn(template, templates)
                templates.add(template)

    def test_all_audio_deliveries(self):
        for mode in app.TargetFormat:
            if not mode.is_video:
                with self.subTest(mode=mode):
                    result = self.execute(self.job(mode))
                    self.assertEqual(result.status, app.Status.SUCCESS, result.error)
                    self.assertTrue(result.path.is_file())
                    self.assertGreater(result.size, 0)

    def test_all_video_deliveries(self):
        for mode in app.TargetFormat:
            if mode.is_video:
                filename = {app.TargetFormat.VIDEO_AV1: "av1.mkv",
                            app.TargetFormat.VIDEO_VP9: "vp9.webm"}.get(mode, "h264.mp4")
                with self.subTest(mode=mode):
                    result = self.execute(self.job(mode, filename, 360))
                    self.assertEqual(result.status, app.Status.SUCCESS, result.error)
                    self.assertTrue(result.path.is_file())

    def test_real_separate_stream_merge(self):
        builder = app.ytdlp_command
        for mode, codec, filename in ((app.TargetFormat.VIDEO_AV1, "av1", "av1.mkv"),
                                      (app.TargetFormat.VIDEO_VP9, "vp9", "vp9.webm"),
                                      (app.TargetFormat.VIDEO, "h264", "h264.mp4")):
            with self.subTest(mode=mode):
                # Strip audio so FFmpeg must merge two actual HTTP downloads.
                silent = self.media / f"silent-{filename}"
                subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.media / filename),
                                "-an", "-c:v", "copy", str(silent)], check=True, timeout=15)
                info = self.root / "merge.json"
                info.write_text(json.dumps({"id": "merge", "title": "Merge fixture", "extractor": "generic",
                    "extractor_key": "Generic", "webpage_url": self.base_url,
                    "formats": [{"format_id": "video", "url": f"{self.base_url}/{silent.name}",
                                 "ext": silent.suffix[1:], "vcodec": codec, "acodec": "none", "height": 96},
                                {"format_id": "audio", "url": f"{self.base_url}/audio.m4a", "ext": "m4a",
                                 "vcodec": "none", "acodec": "aac"}]}), encoding="utf-8")
                def loaded(*args):
                    return builder(*args)[:-2] + ["--load-info-json", str(info)]
                with patch.object(app, "ytdlp_command", loaded):
                    result = self.execute(self.job(mode, f"merge-{codec}", 360))
                self.assertEqual(result.status, app.Status.SUCCESS, result.error)
                metadata = json.loads(subprocess.check_output([
                    "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json",
                    str(result.path)]))
                self.assertEqual({s["codec_type"] for s in metadata["streams"]}, {"audio", "video"})

    def test_cover_art_is_not_a_video_track(self):
        result = self.execute(self.job(app.TargetFormat.AUDIO_BEST, "cover.mp3"))
        self.assertEqual(result.status, app.Status.SUCCESS, result.error)

    def test_silent_video_deliveries(self):
        silent = self.media / "silent-av1.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(self.media / "av1.mkv"),
                        "-an", "-c:v", "copy", str(silent)], check=True, timeout=15)
        for mode in (app.TargetFormat.VIDEO_AV1, app.TargetFormat.VIDEO_BEST, app.TargetFormat.VIDEO_MKV):
            with self.subTest(mode=mode):
                result = self.execute(self.job(mode, silent.name))
                self.assertEqual(result.status, app.Status.SUCCESS, result.error)

    def test_duplicate_and_conflicting_publication(self):
        for move in (False, True):
            with self.subTest(move=move):
                target_dir = self.root / f"target-{move}"
                target_dir.mkdir()
                source = self.destination / "collision.m4a"
                source.write_bytes(b"same")
                target = target_dir / source.name
                target.write_bytes(b"same")
                output, existed = app.publish(source, target_dir, move_to=target_dir if move else None)
                self.assertTrue(existed)
                self.assertEqual(output, target)
                source.write_bytes(b"different")
                with self.assertRaisesRegex(ValueError, "collision"):
                    app.publish(source, target_dir, move_to=target_dir if move else None)
                self.assertEqual(target.read_bytes(), b"same")
                self.assertTrue(source.exists())

    def test_malformed_receipt_and_url_list_updates(self):
        receipt = self.root / "bad-receipt.json"
        for contents in ("{", "null", "[]", '{"path": null}'):
            receipt.write_text(contents, encoding="utf-8")
            self.assertIsNone(app.completed_output(receipt))
        urls = self.root / "urls.txt"
        urls.write_text("https://example.com/one\nhttps://example.com/two\n", encoding="utf-8")
        urls.chmod(0o640)
        app.mark_url_downloaded(urls, "https://example.com/one")
        self.assertEqual(urls.read_text(), "# DOWNLOADED https://example.com/one\nhttps://example.com/two\n")
        self.assertEqual(urls.stat().st_mode & 0o777, 0o640)

    def test_wrong_codec_and_height_are_not_archived(self):
        for job in (self.job(app.TargetFormat.VIDEO_AV1), self.job(app.TargetFormat.VIDEO_BEST, height=60)):
            with self.subTest(job=job):
                result = self.execute(job)
                self.assertEqual(result.status, app.Status.FAILED)
                self.assertEqual(app.archive_path(self.state, job.mode).read_bytes(), b"")
                self.assertIsNone(app.completed_output(app.receipt_path(self.state, job, self.destination)))

    def test_missing_hls_fragment_fails_without_archiving(self):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(self.media / "h264.mp4"),
                        "-c", "copy", "-f", "mpegts", str(self.media / "segment.ts")], check=True, timeout=15)
        (self.media / "broken.m3u8").write_text(
            "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:0.4,\nsegment.ts\n"
            "#EXTINF:0.4,\nmissing.ts\n#EXT-X-ENDLIST\n", encoding="utf-8")
        builder = app.ytdlp_command
        def no_retries(*args):
            command = builder(*args)
            return command[:-2] + ["--fragment-retries", "0", "--retries", "0"] + command[-2:]
        job = self.job(app.TargetFormat.VIDEO_BEST, "broken.m3u8")
        with patch.object(app, "ytdlp_command", no_retries):
            result = self.execute(job)
        self.assertEqual(result.status, app.Status.FAILED, result.error)
        self.assertEqual(app.archive_path(self.state, job.mode).read_bytes(), b"")
        self.assertIsNone(app.completed_output(app.receipt_path(self.state, job, self.destination)))

    def test_ffmpeg_skipped_fragment_is_not_published(self):
        # Exercise the fallback that would otherwise return success for truncated media.
        builder = app.ytdlp_command
        def ffmpeg_hls(*args):
            command = builder(*args)
            return command[:-2] + ["--downloader", "m3u8:ffmpeg", "--fragment-retries", "0"] + command[-2:]
        with patch.object(app, "ytdlp_command", ffmpeg_hls):
            self.test_missing_hls_fragment_fails_without_archiving()
        self.assertFalse(any((self.destination / ".dusky-work").rglob("*.mp4")))
        # Retrying must download again rather than publish the truncated finalized file.
        self.test_missing_hls_fragment_fails_without_archiving()

    def test_complete_hls_download(self):
        segment = self.media / "complete-segment.ts"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.media / "h264.mp4"),
                        "-c", "copy", "-f", "mpegts", str(segment)], check=True, timeout=15)
        (self.media / "complete.m3u8").write_text(
            "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:0.4,\ncomplete-segment.ts\n"
            "#EXT-X-ENDLIST\n", encoding="utf-8")
        result = self.execute(self.job(app.TargetFormat.VIDEO_BEST, "complete.m3u8"))
        self.assertEqual(result.status, app.Status.SUCCESS, result.error)

    def test_receipt_requires_present_unchanged_file_and_matching_profile(self):
        job = self.job(app.TargetFormat.VIDEO_BEST, height=360)
        first = self.execute(job)
        self.assertEqual(first.status, app.Status.SUCCESS, first.error)
        with patch.object(app, "ytdlp_command", side_effect=AssertionError("must skip")):
            repeated = self.execute(job)
        self.assertEqual(repeated.status, app.Status.SKIPPED, repeated.error)
        first.path.unlink()
        again = self.execute(job)
        self.assertEqual(again.status, app.Status.SUCCESS, again.error)
        higher = self.execute(self.job(app.TargetFormat.VIDEO_BEST, height=720))
        self.assertEqual(higher.status, app.Status.SUCCESS, higher.error)
        self.assertNotEqual(higher.path, again.path)
        other_destination = self.root / "another-destination"
        other_destination.mkdir()
        moved = self.execute(job, move_to=other_destination)
        self.assertEqual(moved.status, app.Status.SUCCESS, moved.error)
        self.assertEqual(moved.path.parent, other_destination)
        receipt = app.receipt_path(self.state, job, other_destination)
        moved.path.write_bytes(b"changed")
        self.assertIsNone(app.completed_output(receipt))

    def test_concurrent_move_collision_does_not_replace_output(self):
        source = self.destination / "race.mp4"
        source.write_bytes(b"new media")
        target_dir = self.root / "final"
        target_dir.mkdir()
        target = target_dir / source.name
        rename = app.rename_no_replace
        def collision(old, new):
            target.write_bytes(b"other writer")
            return rename(old, new)
        with patch.object(app, "rename_no_replace", collision):
            with self.assertRaisesRegex(ValueError, "collision"):
                app.publish(source, self.destination, move_to=target_dir)
        self.assertEqual(target.read_bytes(), b"other writer")
        self.assertTrue(source.exists())
        self.assertEqual(list(target_dir.glob(".dusky-part-*")), [])

    def test_parallel_queue(self):
        jobs = [self.job(app.TargetFormat.AUDIO_MP3), self.job(app.TargetFormat.VIDEO_AV1, "av1.mkv"),
                self.job(app.TargetFormat.VIDEO_VP9, "vp9.webm")]
        results, reason = app.run_queue(jobs, self.destination, self.state, app.CookieChoice(), 3)
        self.assertEqual(reason, "")
        self.assertEqual([r.status for r in results], [app.Status.SUCCESS] * 3,
                         [r.error for r in results])

    def test_skip_reaps_process(self):
        with app.progress_display() as progress:
            task = progress.add_task("", title="test", metric="")
            controller = app.QueueController([app.Slot(1, task)])
            slot = controller.claim("sleeper")
            proc = controller.spawn(slot, [sys.executable, "-c", "import time; time.sleep(30)"], self.root)
            timer = threading.Timer(0.2, controller.skip_one, args=(1,))
            timer.start()
            try:
                code = app.pump(proc, slot, controller, app.ProgressParser("sleep"), progress)
                self.assertNotEqual(code, 0)
                self.assertIsNotNone(proc.poll())
            finally:
                timer.cancel()
                timer.join(timeout=2)
                controller.reap(slot, proc)


if __name__ == "__main__":
    unittest.main()
