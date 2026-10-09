"""Missing-codec regression tests; use isolated metadata and local media."""
import contextlib
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import struct
import threading
import time
import tempfile
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).with_name('mpv_yt_dlp_playback_livestream.py')
spec = importlib.util.spec_from_file_location('player', SOURCE)
player = importlib.util.module_from_spec(spec)
spec.loader.exec_module(player)


class CodecMetadata(unittest.TestCase):
    def setUp(self):
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.enterContext(patch.object(player.shutil, 'which', return_value='/usr/bin/ffprobe'))

    def test_known_metadata_needs_no_probe(self):
        info = {'formats': [{'url': 'https://example.com/video', 'vcodec': 'av01', 'acodec': 'none'}]}
        with patch.object(player.subprocess, 'run') as run:
            player.probe_missing_codecs(info)
            run.assert_not_called()

    def test_probe_cli_preserves_format_short_option(self):
        for flag in ('-p', '--probe'):
            args = player.build_parser().parse_args(['https://example.com/video', flag, '-f', '#2'])
            self.assertTrue(args.probe)
            self.assertEqual(args.format, '#2')
        self.assertFalse(player.build_parser().parse_args(['https://example.com/video']).probe)

    def test_fast_retains_successes_and_skips_python_network_reads(self):
        info = {'formats': [
            {'url': 'https://example.com/known', 'vcodec': 'vp9', 'acodec': 'opus'},
            {'url': 'https://example.com/quick.mp4', 'ext': 'mp4'},
            {'url': 'https://example.com/failed', 'protocol': 'm3u8_native'}]}

        def probe(cmd, **kwargs):
            self.assertGreater(kwargs['timeout'], 0)
            self.assertLessEqual(kwargs['timeout'], 3)
            if cmd[-1].endswith('/failed'):
                raise subprocess.TimeoutExpired(cmd, kwargs['timeout'])
            return Mock(returncode=0, stdout='{"streams": [{"codec_type": "video", "codec_name": "h264"},'
                                            '{"codec_type": "audio", "codec_name": "aac"}]}')

        with patch.object(player.subprocess, 'run', side_effect=probe) as run, \
                patch.object(player.urllib.request, 'urlopen') as urlopen:
            player.probe_missing_codecs(info)
        urlopen.assert_not_called()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(info['formats'][0]['vcodec'], 'vp9')
        self.assertEqual(info['formats'][1]['vcodec'], 'h264')
        self.assertNotIn('vcodec', info['formats'][2])

    def test_fast_stalled_probes_share_one_budget_and_are_reaped(self):
        # Use real child processes: a socket/read timeout alone is insufficient
        # to bound the entire operation, including queued formats.
        real_run = subprocess.run
        processes = []
        real_popen = subprocess.Popen

        def track_process(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        def stall(cmd, **kwargs):
            return real_run([player.sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)

        info = {'formats': [{'url': f'https://example.com/{i}.mp4', 'ext': 'mp4'} for i in range(12)]}
        started = time.monotonic()
        with patch.object(player.subprocess, 'run', side_effect=stall), \
                patch.object(player.subprocess, 'Popen', side_effect=track_process), \
                patch.object(player.urllib.request, 'urlopen') as urlopen:
            player.probe_missing_codecs(info)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 2.8)
        self.assertLess(elapsed, 4.5)
        self.assertEqual(len(processes), 4)
        self.assertTrue(all(process.poll() is not None for process in processes))
        urlopen.assert_not_called()

    def test_main_uses_quick_default_and_explicit_long_probe(self):
        for flags, fast in [(['-F'], True), (['-F', '-p'], False),
                            (['-F', '--probe'], False), (['--probe', '-f', 'best', '--print-cmds'], False)]:
            with self.subTest(flags=flags), \
                    patch.object(player.sys, 'argv', ['player', 'https://example.com/video', *flags]), \
                    patch.object(player, 'load_config', return_value={}), \
                    patch.object(player, 'pick_tmpfs', return_value='/dev/shm'), \
                    patch.object(player, 'run_yt_dlp_json', return_value={'formats': []}), \
                    patch.object(player, 'probe_missing_codecs') as probe, \
                    patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(player.main(), 0)
            self.assertEqual(probe.call_args.kwargs['fast'], fast)

    def test_help_plain_and_terminal_rendering(self):
        parser = player.build_parser()
        plain = io.StringIO()
        parser.print_help(file=plain)
        self.assertNotIn('\x1b[', plain.getvalue())
        for text in ('Formats and codec detection:', '-p, --probe', 'Examples:',
                     'default: 3 seconds', 'Codec budgets apply after yt-dlp'):
            self.assertIn(text, plain.getvalue())
        terminal = io.StringIO()
        with patch.object(terminal, 'isatty', return_value=True), \
                patch.object(player, '_RICH', False):
            parser.print_help(file=terminal)
        self.assertEqual(terminal.getvalue(), plain.getvalue())
        if player._RICH:
            terminal = io.StringIO()
            with patch.object(terminal, 'isatty', return_value=True), \
                    patch.dict(os.environ, {'TERM': 'xterm-256color'}):
                parser.print_help(file=terminal)
            # Rich styling must preserve flags and explanations, including brackets.
            visible = player.Text.from_ansi(terminal.getvalue()).plain
            self.assertEqual(visible, plain.getvalue())

    def test_hls_options_do_not_leak_into_other_protocols(self):
        result = Mock(returncode=0, stdout='{"streams": []}')
        for protocol in ('m3u8', 'm3u8_native', 'https', 'http_dash_segments'):
            with self.subTest(protocol=protocol), \
                    patch.object(player.subprocess, 'run', return_value=result) as run:
                player.probe_missing_codecs({'formats': [
                    {'url': 'https://example.com/video', 'protocol': protocol}]})
                cmd = run.call_args.args[0]
                self.assertEqual('-extension_picky' in cmd, protocol.startswith('m3u8'))
                if protocol.startswith('m3u8'):
                    self.assertEqual(cmd[cmd.index('-extension_picky') + 1], '0')

    def test_real_hls_segments_with_tar_url_and_ts_query(self):
        # Match Rumble's URL layout, while serving actual MPEG-TS bytes locally.
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            media = Path(directory) / 'segment.ts'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10',
                            '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '0.5',
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac',
                            '-f', 'mpegts', str(media)], check=True, timeout=15)
            segment = media.read_bytes()
            playlist = (b'#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n'
                        b'#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:0.5,\n'
                        b'archive.tar?r_file=media-0.ts&r_type=video%2Fmp2t\n#EXT-X-ENDLIST\n')

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.headers.get('Referer') != 'https://example.com/':
                        self.send_error(403)
                        return
                    is_playlist = 'r_file=chunklist.m3u8' in self.path
                    body = playlist if is_playlist else segment
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/vnd.apple.mpegurl' if is_playlist else 'video/mp2t')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    pass

            with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    url = f'http://127.0.0.1:{server.server_port}/archive.tar?r_file=chunklist.m3u8'
                    # Confirm the original failure, then exercise the production fix.
                    original = subprocess.run(['ffprobe', '-v', 'error', '-headers',
                                               'Referer: https://example.com/\r\n', '-i', url],
                                              capture_output=True, text=True, timeout=10)
                    self.assertNotEqual(original.returncode, 0)
                    self.assertIn('allowed_segment_extensions', original.stderr)
                    fmt = {'url': url, 'protocol': 'm3u8_native', 'ext': 'mp4',
                           'http_headers': {'Referer': 'https://example.com/'}}
                    player.probe_missing_codecs({'formats': [fmt]})
                    self.assertEqual((fmt['vcodec'], fmt['acodec']), ('h264', 'aac'))
                finally:
                    server.shutdown()
                    worker.join(timeout=5)

    def test_missing_codecs_fill_table_and_codec_selection(self):
        info = {'formats': [{'url': 'https://example.com/video', 'format_id': 'mp4-1080p',
                             'vcodec': None, 'acodec': None}]}
        result = Mock(returncode=0, stdout=json.dumps({'streams': [
            {'codec_type': 'video', 'codec_name': 'h264'},
            {'codec_type': 'audio', 'codec_name': 'aac'}]}))
        with patch.object(player.subprocess, 'run', return_value=result):
            player.probe_missing_codecs(info)
        formats = player.fmt_list(info)
        self.assertEqual((formats[0]['fam'], formats[0]['vcodec'], formats[0]['acodec']),
                         ('avc', 'h264', 'aac'))
        self.assertEqual(player.resolve_format(formats, 'avc'), 'mp4-1080p')

    def test_headers_cookies_environment_and_timeout(self):
        info = {'http_headers': {'User-Agent': 'test', 'Referer': 'https://example.com/'},
                'formats': [{'url': 'https://example.com/video', 'http_headers': {'User-Agent': 'override'},
                             'cookies': 'name=value; domain=example.com; path=/'}]}
        result = Mock(returncode=0, stdout='{"streams": []}')
        env = dict(os.environ)
        with patch.object(player.subprocess, 'run', return_value=result) as run:
            player.probe_missing_codecs(info, env=env)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index('-headers') + 1],
                         'User-Agent: override\r\nReferer: https://example.com/\r\n')
        self.assertEqual(cmd[cmd.index('-cookies') + 1], info['formats'][0]['cookies'])
        self.assertIs(run.call_args.kwargs['env'], env)
        self.assertGreater(run.call_args.kwargs['timeout'], 0)
        self.assertLessEqual(run.call_args.kwargs['timeout'], 20)

    def test_explicit_absent_audio_remains_authoritative(self):
        fmt = {'url': 'https://example.com/video', 'acodec': 'none'}
        result = Mock(returncode=0, stdout=json.dumps({'streams': [
            {'codec_type': 'video', 'codec_name': 'h264'},
            {'codec_type': 'audio', 'codec_name': 'aac'}]}))
        with patch.object(player.subprocess, 'run', return_value=result):
            player.probe_missing_codecs({'formats': [fmt]})
        self.assertEqual(fmt['acodec'], 'none')
        self.assertEqual(fmt['vcodec'], 'h264')

    def test_probe_failures_do_not_prevent_playback(self):
        for response in (subprocess.TimeoutExpired('ffprobe', 20),
                         Mock(returncode=1, stdout=''),
                         Mock(returncode=0, stdout='invalid JSON'),
                         Mock(returncode=0, stdout='{"streams": []}')):
            with self.subTest(response=response):
                fmt = {'url': 'https://example.com/video'}
                with patch.object(player.subprocess, 'run') as run:
                    if isinstance(response, Exception):
                        run.side_effect = response
                    else:
                        run.return_value = response
                    player.probe_missing_codecs({'formats': [fmt]})
                self.assertNotIn('vcodec', fmt)
                self.assertNotIn('acodec', fmt)

    def test_missing_ffprobe_is_optional(self):
        fmt = {'url': 'https://example.com/video'}
        with patch.object(player.shutil, 'which', return_value=None), \
                patch.object(player.subprocess, 'run') as run:
            player.probe_missing_codecs({'formats': [fmt]})
            run.assert_not_called()
        self.assertNotIn('vcodec', fmt)

    def test_shared_deadline_skips_queued_work(self):
        with patch.object(player.time, 'monotonic', side_effect=[0, 21]), \
                patch.object(player.subprocess, 'run') as run:
            player.probe_missing_codecs({'formats': [{'url': 'https://example.com/video'}]})
            run.assert_not_called()

    def test_real_mp4_header_detection(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            media = Path(directory) / 'test.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10',
                            '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '0.5',
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac',
                            '-movflags', '+faststart', str(media)], check=True, timeout=15)
            fmt = {'url': str(media)}
            player.probe_missing_codecs({'formats': [fmt]})
            self.assertEqual((fmt['vcodec'], fmt['acodec']), ('h264', 'aac'))
            data = media.read_bytes()
            received = []

            def serve(request, timeout):
                start, end = map(int, request.get_header('Range')[6:].split('-'))
                response = io.BytesIO(data[start:end + 1])
                response.status = 206
                response.headers = {'Content-Range': f'bytes {start}-{min(end, len(data)-1)}/{len(data)}'}
                received.append((start, end))
                return response

            fmt = {'url': 'https://example.com/video.mp4', 'ext': 'mp4'}
            with patch.object(player.urllib.request, 'urlopen', side_effect=serve):
                player.probe_missing_codecs({'formats': [fmt]}, fast=False)
            self.assertEqual((fmt['vcodec'], fmt['acodec']), ('h264', 'aac'))
            self.assertTrue(received)

    def test_range_rejection_falls_back_to_ffprobe(self):
        response = io.BytesIO(b'')
        response.status = 200
        result = Mock(returncode=0, stdout='{"streams": [{"codec_type": "video", "codec_name": "vp9"}]}')
        fmt = {'url': 'https://example.com/video.mp4', 'ext': 'mp4'}
        with patch.object(player.urllib.request, 'urlopen', return_value=response), \
                patch.object(player.subprocess, 'run', return_value=result) as run:
            player.probe_missing_codecs({'formats': [fmt]}, fast=False)
        self.assertEqual(fmt['vcodec'], 'vp9')
        self.assertEqual(fmt['acodec'], 'none')
        self.assertEqual(run.call_args.args[0][-1], fmt['url'])

    def test_interrupted_header_response_falls_back(self):
        result = Mock(returncode=0, stdout='{"streams": [{"codec_type": "video", "codec_name": "h264"}]}')
        fmt = {'url': 'https://example.com/video.mp4', 'ext': 'mp4'}
        with patch.object(player.urllib.request, 'urlopen',
                          side_effect=player.http.client.IncompleteRead(b'')), \
                patch.object(player.subprocess, 'run', return_value=result):
            player.probe_missing_codecs({'formats': [fmt]}, fast=False)
        self.assertEqual(fmt['vcodec'], 'h264')

    def test_sparse_header_skips_huge_index_and_supports_extended_sizes(self):
        def box(kind, payload):
            return struct.pack('>I4s', len(payload) + 8, kind) + payload

        huge_index_size = 10_000_000
        description = box(b'stsd', b'codec descriptor')
        index = struct.pack('>I4s', huge_index_size, b'stco')
        # Simulate a giant video sample index before the audio track.
        stbl = struct.pack('>I4s', 8 + len(description) + huge_index_size, b'stbl') + description + index
        audio = box(b'trak', box(b'mdia', box(b'minf', box(b'stbl', description))))
        # Put stbl at the proper minf/mdia nesting level.
        stbl_size = 8 + len(description) + huge_index_size
        video_prefix = (struct.pack('>I4s', stbl_size + 24, b'trak') +
                        struct.pack('>I4s', stbl_size + 16, b'mdia') +
                        struct.pack('>I4s', stbl_size + 8, b'minf') + stbl)
        payload = video_prefix + bytes(huge_index_size - 8) + audio
        data = struct.pack('>I4sQ', 1, b'moov', len(payload) + 16) + payload
        received = []

        def serve(request, timeout):
            start, end = map(int, request.get_header('Range')[6:].split('-'))
            response = io.BytesIO(data[start:end + 1])
            response.status = 206
            response.headers = {'Content-Range': f'bytes {start}-{min(end, len(data)-1)}/{len(data)}'}
            received.append((start, end))
            return response

        with patch.object(player.urllib.request, 'urlopen', side_effect=serve):
            compact = player.mp4_codec_header('https://example.com/v.mp4', {}, time.monotonic() + 5)
        self.assertEqual(compact.count(b'codec descriptor'), 2)
        self.assertLess(len(compact), 256)
        self.assertLessEqual(len(received), 2)

    def test_invalid_range_and_truncated_boxes_are_rejected(self):
        for content_range, data in [('bytes 99-120/121', b'bad'),
                                    ('bytes 0-2/3', b'bad'),
                                    ('bytes 0-7/8', struct.pack('>I4s', 0, b'moov'))]:
            response = io.BytesIO(data)
            response.status = 206
            response.headers = {'Content-Range': content_range}
            with self.subTest(content_range=content_range), \
                    patch.object(player.urllib.request, 'urlopen', return_value=response):
                with self.assertRaises(ValueError):
                    player.mp4_codec_header('https://example.com/v.mp4', {}, time.monotonic() + 5)


class FormatSelection(unittest.TestCase):
    def youtube_info(self):
        # Match the eight rows in the reported live-stream table.
        formats = []
        for fid, height in [('233', None), ('234', None), ('269', 144), ('229', 240),
                            ('230', 360), ('231', 480), ('232', 720), ('270', 1080)]:
            formats.append({'format_id': fid, 'url': f'https://example.com/{fid}.m3u8',
                            'protocol': 'm3u8_native', 'ext': 'mp4', 'height': height,
                            'vcodec': 'avc1.4D401F' if height else 'none',
                            'acodec': 'none' if height else 'aac',
                            'abr': None if height else (64 if fid == '233' else 128)})
        return {'id': 'test', 'title': 'test live stream', 'extractor': 'youtube',
                'extractor_key': 'Youtube', 'webpage_url': 'https://www.youtube.com/watch?v=test',
                'is_live': True, 'live_status': 'is_live', 'formats': formats}

    def test_prompt_accepts_row_numbers_and_attaches_audio(self):
        formats = player.fmt_list(self.youtube_info())
        for answer in ('6', '#6', '232'):
            with self.subTest(answer=answer), patch.object(player.sys.stdin, 'isatty', return_value=True), \
                    patch('builtins.input', return_value=answer), patch.object(player, 'print_formats'):
                self.assertEqual(player.resolve_format(formats, None), '232+bestaudio/232')

    def test_numeric_ids_win_ties_and_hash_forces_row(self):
        formats = [{'id': '1', 'acodec': 'aac'}, {'id': 'other', 'acodec': 'aac'}]
        for answer, expected in [('1', '1'), ('#1', 'other')]:
            with self.subTest(answer=answer), patch.object(player.sys.stdin, 'isatty', return_value=True), \
                    patch('builtins.input', return_value=answer), patch.object(player, 'print_formats'):
                self.assertEqual(player.resolve_format(formats, None), expected)
        # Explicit CLI/raw selectors retain yt-dlp's format-ID semantics.
        self.assertEqual(player.resolve_format(formats, '0'), '0')

    def test_invalid_prompt_row_reports_local_error(self):
        with patch.object(player.sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', return_value='99'), patch.object(player, 'print_formats'):
            with self.assertRaisesRegex(SystemExit, 'format row.*out of range'):
                player.resolve_format(player.fmt_list(self.youtube_info()), None)

    def test_real_ytdlp_selects_row_video_and_separate_audio(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            metadata = Path(directory) / 'metadata.json'
            info = self.youtube_info()
            metadata.write_text(json.dumps(info))
            cmd = ['yt-dlp', '--ignore-config', '--no-cache-dir', '--skip-download', '-J',
                   '--load-info-json', str(metadata), '--format']
            original = subprocess.run(cmd + ['6'], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(original.returncode, 0)
            self.assertIn('Requested format is not available', original.stderr)
            with patch.object(player.sys.stdin, 'isatty', return_value=True), \
                    patch('builtins.input', return_value='6'), patch.object(player, 'print_formats'):
                choice = player.resolve_format(player.fmt_list(info), None)
            selected = subprocess.run(cmd + [choice], capture_output=True, text=True, timeout=10)
            self.assertEqual(selected.returncode, 0, selected.stderr)
            tracks = json.loads(selected.stdout)['requested_formats']
            self.assertEqual([track['format_id'] for track in tracks], ['232', '234'])


class HistoryAndLive(unittest.TestCase):
    def setUp(self):
        self.output = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def entries(self):
        return [{'url': f'https://example.com/{i}', 'title': f'Video {i}',
                 'mode': 'plain', 'format': 'best', 'buffer': 'near'} for i in range(3)]

    def test_history_selection_quit_empty_and_noninteractive(self):
        for replies, expected in [(['1'], 1), ([''], 0), (['99', '2'], 2), (['q'], None)]:
            with self.subTest(replies=replies), patch.object(player, 'load_history', return_value=self.entries()), \
                    patch.object(player.sys.stdin, 'isatty', return_value=True), \
                    patch('builtins.input', side_effect=replies):
                result = player.pick_history()
                self.assertEqual(result['title'] if result else None,
                                 f'Video {expected}' if expected is not None else None)
        for entries, tty in [([], True), (self.entries(), False)]:
            with patch.object(player, 'load_history', return_value=entries), \
                    patch.object(player.sys.stdin, 'isatty', return_value=tty), patch('builtins.input') as ask:
                self.assertIsNone(player.pick_history())
                ask.assert_not_called()

    def test_history_persists_thirty_unique_urls_and_moves_replays_to_front(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory, \
                patch.object(player, '_cfg_dir', return_value=directory), \
                patch.object(player, 'HISTORY_FILE', str(Path(directory) / 'history.toml')):
            for i in range(32):
                player.remember({'url': f'https://example.com/{i}', 'title': f'Video {i}'})
            history = player.load_history()
            self.assertEqual(len(history), 30)
            self.assertEqual(history[0]['url'], 'https://example.com/31')
            self.assertEqual(history[-1]['url'], 'https://example.com/2')
            player.remember({'url': 'https://example.com/5', 'title': 'Played again'})
            history = player.load_history()
            self.assertEqual(len(history), 30)
            self.assertEqual(history[0]['url'], 'https://example.com/5')
            self.assertEqual(history[0]['plays'], 2)
            self.assertEqual(len({entry['url'] for entry in history}), 30)

    def test_list_replays_selected_url_and_enables_seeking_only_for_live(self):
        for live in (False, True):
            with self.subTest(live=live), \
                    patch.object(player.sys, 'argv', ['vid', 'list', '--print-cmds']), \
                    patch.object(player.sys.stdin, 'isatty', return_value=True), \
                    patch('builtins.input', return_value='1'), \
                    patch.object(player, 'load_history', return_value=self.entries()), \
                    patch.object(player, 'load_config', return_value={}), \
                    patch.object(player, 'pick_tmpfs', return_value='/dev/shm'), \
                    patch.object(player, 'run_yt_dlp_json', return_value={'formats': [], 'is_live': live}) as extract, \
                    patch.dict(os.environ, {}, clear=True), \
                    contextlib.redirect_stderr(io.StringIO()) as diagnostics:
                self.assertEqual(player.main(), 0)
                self.assertEqual(extract.call_args.args[0], 'https://example.com/1')
                self.assertEqual('--force-seekable=yes' in diagnostics.getvalue(), live)
                self.assertEqual('--cache=yes' in diagnostics.getvalue(), live)

    def test_playback_promotes_old_selection_and_last_reuses_it(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory, \
                patch.object(player, '_cfg_dir', return_value=directory), \
                patch.object(player, 'HISTORY_FILE', str(Path(directory) / 'history.toml')), \
                patch.object(player, 'load_config', return_value={}), \
                patch.object(player, 'pick_tmpfs', return_value='/dev/shm'), \
                patch.object(player.sys.stdin, 'isatty', return_value=True), \
                patch.object(player, 'run_yt_dlp_json', return_value={'formats': [], 'title': 'Replayed'}), \
                patch.object(player.subprocess, 'Popen') as launch, \
                patch.dict(os.environ, {}, clear=True):
            launch.return_value.wait.return_value = 0
            launch.return_value.poll.return_value = 0
            player.save_history(self.entries())
            for command, replies in [('list', ['2']), ('last', [])]:
                with patch.object(player.sys, 'argv', ['vid', command]), \
                        patch('builtins.input', side_effect=replies):
                    self.assertEqual(player.main(), 0)
                self.assertEqual(launch.call_args.args[0][-1], 'https://example.com/2')
                history = player.load_history()
                self.assertEqual(history[0]['url'], 'https://example.com/2')
                self.assertEqual(len(history), 3)
            self.assertEqual(history[0]['plays'], 2)
            self.assertEqual(history[0]['format'], 'best')
            self.assertGreater(history[0]['last_played'], 0)

    def test_last_with_empty_history_reports_local_error(self):
        with patch.object(player.sys, 'argv', ['vid', 'last']), \
                patch.object(player, 'load_history', return_value=[]), \
                patch.object(player, 'run_yt_dlp_json') as extract:
            with self.assertRaisesRegex(SystemExit, 'history is empty'):
                player.main()
            extract.assert_not_called()

    def test_real_mpv_travel_rewinds_cached_data_without_reopening(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            root = Path(directory)
            media = root / 'test.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=5',
                            '-t', '120', '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '5',
                            '-movflags', '+faststart', str(media)], check=True, timeout=15)
            travel = root / 'travel'
            travel.mkdir()
            (travel / 'main.lua').write_text(player.TRAVEL_LUA)
            harness = root / 'verify.lua'
            harness.write_text("""
mp.register_event('file-loaded', function()
    mp.commandv('seek', '70', 'absolute+exact')
    mp.add_timeout(0.5, function()
        print('BEFORE_REWIND ' .. tostring(mp.get_property_number('time-pos')))
        mp.commandv('script-binding', 'travel/travel-91')
        mp.add_timeout(0.5, function()
            print('AFTER_REWIND ' .. tostring(mp.get_property_number('time-pos')))
            mp.commandv('quit')
        end)
    end)
end)
""")
            result = subprocess.run(['mpv', '--no-config', '--vo=null', '--ao=null', '--pause',
                                     '--cache=yes', '--force-seekable=yes', '--demuxer-max-bytes=32M',
                                     '--demuxer-max-back-bytes=32M', '--input-terminal=no',
                                     f'--scripts-append={travel}', f'--scripts-append={harness}', str(media)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = result.stdout + result.stderr
            self.assertIn('BEFORE_REWIND 70', output)
            self.assertIn('AFTER_REWIND 10', output)
            # A jump beyond the cache still requests the existing server-reopen path.
            harness.write_text("""
mp.register_event('file-loaded', function()
    mp.commandv('seek', '70', 'absolute+exact')
    mp.add_timeout(0.5, function()
        mp.commandv('script-binding', 'travel/travel-94')
    end)
end)
""")
            outside = subprocess.run(['mpv', '--no-config', '--vo=null', '--ao=null', '--pause',
                                      '--cache=yes', '--input-terminal=no',
                                      f'--scripts-append={travel}', f'--scripts-append={harness}', str(media)],
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(outside.returncode, 94, outside.stdout + outside.stderr)
            state = json.loads((root / 'position.json').read_text())
            self.assertEqual(state['time-pos'], 70)


class ServerDVR(unittest.TestCase):
    def test_playlist_reload_growth_uris_end_and_sliding_window_rejection(self):
        url = 'https://cdn.example.com/live/chunklist_DVR.m3u8'
        initial = ('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:1\n#EXT-X-TARGETDURATION:2\n'
                   '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXT-X-MAP:URI="init.mp4"\n'
                   '#EXTINF:2,\na.ts\n')
        responses = [initial, initial + '#EXTINF:2,\nb.ts\n#EXT-X-ENDLIST\n',
                     initial.replace('SEQUENCE:1', 'SEQUENCE:2')]

        def fetch(request, timeout):
            response = io.BytesIO(responses.pop(0).encode())
            response.geturl = lambda: url
            return response

        # Use a captured real urlopen for the adapter's local HTTP endpoint.
        urlopen = player.urllib.request.urlopen
        with patch.object(player.urllib.request, 'urlopen', side_effect=fetch), \
                contextlib.redirect_stderr(io.StringIO()), \
                player.rumble_dvr_playlist(url, {'Referer': 'test'}) as adapted:
            adapted, live_delay = adapted
            self.assertEqual(live_delay, 2)
            with urlopen(adapted, timeout=3) as response:
                body = response.read().decode()
            self.assertIn('#EXT-X-PLAYLIST-TYPE:EVENT', body)
            self.assertIn('https://cdn.example.com/live/a.ts', body)
            self.assertIn('URI="https://cdn.example.com/live/key.bin"', body)
            self.assertIn('URI="https://cdn.example.com/live/init.mp4"', body)
            with urlopen(adapted, timeout=3) as response:
                self.assertIn('#EXT-X-ENDLIST', response.read().decode())
            with self.assertRaises(player.urllib.request.HTTPError) as error:
                urlopen(adapted, timeout=3)
            self.assertEqual(error.exception.code, 502)
            error.exception.close()

    def test_main_adapts_selected_format_in_plain_and_live_modes(self):
        source = 'https://cdn.example.com/chunklist_DVR.m3u8'
        info = {'id': 'test', 'title': 'DVR test', 'extractor_key': 'RumbleEmbed',
                'is_live': True, 'live_status': 'is_live', 'url': source,
                'format_id': 'hls-2', 'http_headers': {'Referer': 'test'},
                'formats': [{'format_id': 'hls-2', 'url': source, 'vcodec': 'h264', 'acodec': 'aac'}],
                # Current yt-dlp's clean JSON contains filenames here, not tracks.
                'requested_downloads': [{'filename': 'test.mp4'}]}
        for mode in ('plain', 'live'):
            with self.subTest(mode=mode), \
                    patch.object(player.sys, 'argv', ['vid', 'https://rumble.com/test', '--mode', mode,
                                                      '-f', 'best', '--buffer', 'near', '--print-cmds']), \
                    patch.object(player, 'load_config', return_value={}), \
                    patch.object(player, 'pick_tmpfs', return_value='/dev/shm'), \
                    patch.object(player, 'run_yt_dlp_json', return_value=info), \
                    patch.object(player.subprocess, 'run', return_value=Mock(returncode=0, stdout=json.dumps(info))), \
                    patch.object(player, 'rumble_dvr_playlist',
                                 return_value=contextlib.nullcontext(('http://127.0.0.1:1234/dvr.m3u8', 6.0))) as adapt, \
                    patch.object(player.json, 'dump', wraps=json.dump) as dump, \
                    patch.dict(os.environ, {}, clear=True), \
                    contextlib.redirect_stderr(io.StringIO()) as diagnostics:
                self.assertEqual(player.main(), 0)
                adapt.assert_called_once_with(source, {'Referer': 'test'})
                metadata = next(call.args[0] for call in dump.call_args_list
                                if call.args[0].get('url') == 'http://127.0.0.1:1234/dvr.m3u8')
                self.assertEqual(metadata['url'], 'http://127.0.0.1:1234/dvr.m3u8')
                self.assertEqual(metadata['manifest_url'], metadata['url'])
                self.assertIn('ytdl_hook-use_manifests=yes', diagnostics.getvalue())
                self.assertIn('--demuxer-lavf-o-append=live_start_index=0', diagnostics.getvalue())
                self.assertIn('--start=-6.0', diagnostics.getvalue())
                self.assertNotIn('requested_downloads', metadata)
                self.assertIn('load-info-json=', diagnostics.getvalue())
                self.assertEqual('travel-server_seek=yes' in diagnostics.getvalue(), mode == 'live')
                with patch.object(player.sys, 'argv', player.sys.argv + ['--start', '30']), \
                        contextlib.redirect_stderr(io.StringIO()) as explicit:
                    self.assertEqual(player.main(), 0)
                self.assertIn('--start=30', explicit.getvalue())
                self.assertNotIn('--start=-6.0', explicit.getvalue())

    def test_real_mpv_server_rewind_outside_local_cache(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            root = Path(directory)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=5',
                            '-f', 'lavfi', '-i', 'sine=frequency=440',
                            '-t', '240', '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '10', '-c:a', 'aac',
                            '-f', 'hls', '-hls_time', '2', '-hls_list_size', '0', '-start_number', '1',
                            str(root / 'chunklist_DVR.m3u8')], check=True, timeout=15)
            playlist = root / 'chunklist_DVR.m3u8'
            prefix, *segments = playlist.read_text().replace('#EXT-X-ENDLIST\n', '').split('#EXTINF:')
            visible = 60  # Start with two minutes; append a segment on each reload.

            requests = []

            class Handler(SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=directory, **kwargs)

                def do_GET(self):
                    nonlocal visible
                    if self.path != '/chunklist_DVR.m3u8':
                        return super().do_GET()
                    visible = min(visible + 1, len(segments))
                    body = (prefix + ''.join('#EXTINF:' + segment for segment in segments[:visible])).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    requests.append(self.path)

            with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                self.addCleanup(worker.join)
                self.addCleanup(server.shutdown)
                source = f'http://127.0.0.1:{server.server_port}/chunklist_DVR.m3u8'
                harness = root / 'verify.lua'
                harness.write_text("""
mp.register_event('file-loaded', function()
    mp.commandv('seek', '114', 'absolute+exact')
    mp.add_timeout(0.5, function()
        print('EDGE ' .. tostring(mp.get_property_number('time-pos')))
        print('WINDOW ' .. tostring(mp.get_property_number('duration')))
        mp.commandv('script-binding', 'travel/travel-91')
        mp.add_timeout(0.5, function()
            print('SERVER_KEY ' .. tostring(mp.get_property_number('time-pos')))
            mp.commandv('seek', '10', 'absolute+exact')
            local deadline = mp.get_time() + 5
            local timer
            timer = mp.add_periodic_timer(0.1, function()
                local cache = mp.get_property_native('demuxer-cache-state') or {}
                local reader = cache['reader-pts']
                if (reader and reader >= 10 and reader < 20) or mp.get_time() >= deadline then
                    print('AFTER_SEEK ' .. tostring(mp.get_property_number('time-pos')))
                    print('SERVER_DATA ' .. tostring(reader))
                    timer:kill()
                    mp.commandv('quit')
                end
            end)
        end)
    end)
end)
""")
                travel = root / 'travel'
                travel.mkdir()
                (travel / 'main.lua').write_text(player.TRAVEL_LUA)
                command = ['mpv', '--no-config', '--vo=null', '--ao=null', '--pause', '--cache=yes',
                           '--force-seekable=yes', '--demuxer-max-bytes=1M', '--demuxer-max-back-bytes=1M',
                           '--input-terminal=no', f'--scripts-append={harness}']
                original = subprocess.run(command + [source], capture_output=True, text=True, timeout=15)
                before = original.stdout + original.stderr
                self.assertIn('Not seekable, but enabling seeking', before)
                self.assertNotIn('AFTER_SEEK 10', before)
                # Exercise the launcher settings: initialize the origin, start
                # near live, then rewind beyond the small local cache.
                harness.write_text(harness.read_text().replace(
                    "    mp.commandv('seek', '114', 'absolute+exact')\n", ''))
                with player.rumble_dvr_playlist(source, {}) as (adapted, live_delay):
                    fixed = subprocess.run(command + ['--demuxer-lavf-o-append=live_start_index=0',
                                                       f'--start=-{live_delay}',
                                                       f'--scripts-append={travel}',
                                                       '--script-opts-append=travel-server_seek=yes', adapted],
                                           capture_output=True, text=True, timeout=15)
                after = fixed.stdout + fixed.stderr
                self.assertEqual(fixed.returncode, 0, after)
                edge = float(player.re.search(r'EDGE ([0-9.]+)', after)[1])
                window = float(player.re.search(r'WINDOW ([0-9.]+)', after)[1])
                self.assertGreaterEqual(window, 120)
                self.assertAlmostEqual(edge, window - live_delay, delta=1)
                key_position = float(player.re.search(r'SERVER_KEY ([0-9.]+)', after)[1])
                self.assertAlmostEqual(key_position, edge - 60, delta=1)
                self.assertIn('AFTER_SEEK 10', after)
                self.assertRegex(after, r'SERVER_DATA 1[0-9]\.', after + str(requests))

                # Run the actual launcher and yt-dlp hook too, not just a hand
                # assembled mpv command. Only site extraction is substituted.
                info = {'id': 'test', 'title': 'DVR test', 'extractor_key': 'RumbleEmbed',
                        'is_live': True, 'live_status': 'is_live', 'webpage_url': source,
                        'formats': [{'format_id': 'hls-2', 'url': source, 'ext': 'mp4',
                                     'protocol': 'm3u8_native', 'vcodec': 'h264', 'acodec': 'aac'}]}
                real_popen = subprocess.Popen
                for mode in ('plain', 'live'):
                    with self.subTest(launcher_mode=mode), tempfile.TemporaryFile(mode='w+') as log:
                        def launch(cmd, **kwargs):
                            if Path(cmd[0]).name == 'mpv':
                                kwargs.update(stdout=log, stderr=log)
                            return real_popen(cmd, **kwargs)

                        extra = ['--no-config', '--vo=null', '--ao=null', '--pause',
                                 '--input-terminal=no', '--demuxer-max-bytes=1M',
                                 '--demuxer-max-back-bytes=1M', f'--scripts-append={harness}']
                        if mode == 'plain':
                            extra += [f'--scripts-append={travel}', '--script-opts-append=travel-server_seek=yes']
                        with patch.object(player.sys, 'argv', ['vid', source, '--mode', mode,
                                                               '-f', 'best', '--buffer', 'near',
                                                               '--player-args', player.shlex.join(extra)]), \
                                patch.object(player, 'load_config', return_value={}), \
                                patch.object(player, '_cfg_dir', return_value=str(root / 'prefs')), \
                                patch.object(player, 'HISTORY_FILE', str(root / 'prefs/history.toml')), \
                                patch.object(player, 'pick_tmpfs', return_value=directory), \
                                patch.object(player, 'run_yt_dlp_json', return_value=info), \
                                patch.object(player.subprocess, 'Popen', side_effect=launch), \
                                patch.dict(os.environ, {}, clear=True), \
                                contextlib.redirect_stderr(io.StringIO()):
                            self.assertEqual(player.main(), 0)
                        log.seek(0)
                        output = log.read()
                        self.assertIn('AFTER_SEEK 10', output)
                        self.assertRegex(output, r'SERVER_DATA 1[0-9]\.', output)


if __name__ == '__main__':
    unittest.main()
