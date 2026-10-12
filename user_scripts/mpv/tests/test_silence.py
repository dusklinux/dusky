"""Exercise the real mpv Lua script and decoded audio; no mocked filter logic.

Run with Python 3.15+, mpv and ffmpeg installed:
    python3 -m unittest discover -s user_scripts/mpv/tests -v
"""
import array
import functools
import http.server
import json
import math
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'setup/assets/scripts/dusky_silence.lua'


class SilenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mpv-silence-test-')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def fixture(self, rate=48000, channels=1, quiet=0, gap=2, tail=0):
        """Speech-like tones surrounding a gap; optional preserved silent tail."""
        samples = array.array('h')
        for seconds, amplitude in [(2, 0.12), (gap, quiet), (8, 0.12), (tail, 0)]:
            # A repeated 10ms period avoids a large Python per-sample loop.
            period = array.array('h', (
                int(32767 * amplitude * math.sin(2 * math.pi * 400 * i / rate))
                for i in range(rate // 100)
                for _ in range(channels)))
            samples.extend(period * round(seconds * 100))
        path = self.folder / f'input-{rate}-{channels}-{quiet}-{gap}-{tail}.wav'
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(samples.tobytes())
        return path

    def render(self, path, level='max', extra=()):
        output = self.folder / 'output.wav'
        result = subprocess.run([
            'mpv', '--no-config', '--vo=null', '--ao=pcm', '--untimed', '--audio-format=s16',
            f'--ao-pcm-file={output}', f'--script={SCRIPT}',
            f'--script-opts=dusky_silence-level={level}', *extra, str(path),
        ], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('failed', result.stdout.lower() + result.stderr.lower())
        self.assertNotIn('Lua error', result.stdout + result.stderr)
        with wave.open(str(output), 'rb') as audio:
            seconds = audio.getnframes() / audio.getframerate()
            pcm = audio.readframes(audio.getnframes())
            return seconds, pcm

    def test_rates_channels_levels_and_end_preservation(self):
        for rate in (16000, 44100, 48000, 96000):
            for channels in (1, 2):
                for level, blocks in [('mild', 28), ('medium', 24), ('max', 4), ('max-plus', 1)]:
                    with self.subTest(rate=rate, channels=channels, level=level):
                        path = self.fixture(rate, channels, tail=3)
                        seconds, pcm = self.render(path, level)
                        # Only the interior gap is shortened; 10s of tone and
                        # the exact 3s silent ending must remain in the output.
                        retained = min(2, blocks * 1152 / rate)
                        self.assertAlmostEqual(seconds, 13 + retained, delta=0.045)
                        self.assertEqual(pcm[-rate * channels * 2 * 3:],
                                         bytes(rate * channels * 2 * 3))

    def test_short_pauses_and_quiet_speech_are_retained(self):
        for quiet, gap in [(0, 0.04), (0.012, 2), (0.12, 2)]:
            with self.subTest(quiet=quiet, gap=gap):
                seconds, _ = self.render(self.fixture(quiet=quiet, gap=gap))
                self.assertAlmostEqual(seconds, 10 + gap, delta=0.015)

    def test_off_short_files_and_video_are_untouched(self):
        path = self.fixture()
        seconds, _ = self.render(path, extra=['--script-opts-append=dusky_silence-enabled=no'])
        self.assertAlmostEqual(seconds, 12, delta=0.001)
        short = self.folder / 'short.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-t', '5',
                        str(short)], check=True)
        seconds, _ = self.render(short)
        self.assertAlmostEqual(seconds, 5, delta=0.001)
        video = self.folder / 'video.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=black:s=32x32:r=1:d=12', '-i', str(path),
                        '-c:v', 'ffv1', '-c:a', 'copy', str(video)], check=True)
        seconds, _ = self.render(video, extra=['--vid=no'])
        self.assertAlmostEqual(seconds, 12, delta=0.001)

    def test_compressed_formats_cover_art_and_network(self):
        path = self.fixture()
        for extension, codec in [('mp3', 'libmp3lame'), ('flac', 'flac'),
                                 ('opus', 'libopus'), ('m4a', 'aac')]:
            with self.subTest(format=extension):
                encoded = self.folder / f'encoded.{extension}'
                subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path),
                                '-c:a', codec, str(encoded)], check=True)
                seconds, _ = self.render(encoded)
                self.assertAlmostEqual(seconds, 10.096, delta=0.075)
        cover = self.folder / 'cover.png'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=red:s=32x32', '-frames:v', '1', str(cover)], check=True)
        artwork = self.folder / 'artwork.mp3'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.folder / 'encoded.mp3'),
                        '-i', str(cover), '-map', '0:a', '-map', '1:v', '-c', 'copy',
                        '-disposition:v', 'attached_pic', str(artwork)], check=True)
        seconds, _ = self.render(artwork)
        self.assertAlmostEqual(seconds, 10.096, delta=0.075)

        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(
            ('127.0.0.1', 0), functools.partial(QuietHandler, directory=self.tmp.name))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            seconds, _ = self.render(f'http://127.0.0.1:{server.server_port}/encoded.mp3')
            self.assertAlmostEqual(seconds, 10.096, delta=0.075)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=10)

    def test_long_gaps_and_unknown_duration_pipe(self):
        seconds, _ = self.render(self.fixture(gap=90))
        self.assertAlmostEqual(seconds, 10.096, delta=0.045)
        output = self.folder / 'pipe.wav'
        path = self.fixture()
        with wave.open(str(path), 'rb') as source:
            pcm = source.readframes(source.getnframes())
        result = subprocess.run([
            'mpv', '--no-config', '--vo=null', '--ao=pcm', '--audio-format=s16',
            '--untimed', f'--ao-pcm-file={output}', f'--script={SCRIPT}',
            '--demuxer=rawaudio', '--demuxer-rawaudio-rate=48000',
            '--demuxer-rawaudio-channels=mono', '--demuxer-rawaudio-format=s16le', '-',
        ], input=pcm, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with wave.open(str(output), 'rb') as audio:
            self.assertAlmostEqual(audio.getnframes() / audio.getframerate(),
                                   10.096, delta=0.045)

    def start_ipc(self, path, extra=()):
        ipc = self.folder / 'ipc.sock'
        log = (self.folder / 'mpv.log').open('w', encoding='utf-8')
        self.addCleanup(log.close)
        process = subprocess.Popen([
            'mpv', '--no-config', '--vo=null', '--ao=null', '--pause',
            '--idle=yes', '--keep-open=yes', f'--input-ipc-server={ipc}',
            f'--script={SCRIPT}', '--af=volume=0.8', *extra, str(path),
        ], stdout=log, stderr=log)

        def stop():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
        self.addCleanup(stop)
        deadline = time.monotonic() + 10
        while not ipc.exists():
            if time.monotonic() > deadline:
                self.fail('mpv did not create IPC socket')
            time.sleep(0.01)
        connection = socket.socket(socket.AF_UNIX)
        connection.settimeout(10)
        connection.connect(str(ipc))
        self.addCleanup(connection.close)
        stream = connection.makefile('rwb')
        self.addCleanup(stream.close)

        def command(*args):
            stream.write(json.dumps({'command': args, 'request_id': 1}).encode() + b'\n')
            stream.flush()
            while True:
                reply = json.loads(stream.readline())
                if reply.get('request_id') == 1:
                    self.assertEqual(reply['error'], 'success', reply)
                    return reply.get('data')
        return command

    def wait_for(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('mpv did not reach expected state')
            time.sleep(0.02)

    def test_toggle_seek_speed_playlist_and_other_filters(self):
        path = self.fixture()
        command = self.start_ipc(path)

        def trimming():
            return [f for f in command('get_property', 'af')
                    if f.get('label') == 'dusky_silence']

        self.wait_for(lambda: len(trimming()) == 1)
        for _ in range(20):
            command('script-binding', 'dusky_silence/toggle')
            self.wait_for(lambda: not trimming())
            command('script-binding', 'dusky_silence/toggle')
            self.wait_for(lambda: len(trimming()) == 1)
        for pos in [8, 1, 5, 0, 11]:
            command('seek', pos, 'absolute+exact')
            self.wait_for(lambda: abs(command('get_property', 'time-pos') - pos) < 0.1)
        for speed in [0.5, 1.5, 2, 1]:
            command('set_property', 'speed', speed)
            self.assertEqual(command('get_property', 'speed'), speed)
        # Cycling settings must not duplicate filters or replace a user's EQ.
        for _ in range(10):
            command('script-binding', 'dusky_silence/cycle')
            time.sleep(0.03)
        filters = command('get_property', 'af')
        self.assertEqual(sum(f['name'] == 'volume' for f in filters), 1)
        self.assertLessEqual(len(trimming()), 1)
        command('loadfile', str(self.fixture(rate=44100)), 'replace')
        def new_rate():
            filters = trimming()
            return len(filters) == 1 and 'window=0.026122449' in filters[0]['params']['graph']
        self.wait_for(new_rate)

    def test_max_plus_shortens_pauses_without_raising_threshold(self):
        path = self.fixture()
        standard, standard_pcm = self.render(path)
        plus, plus_pcm = self.render(path, 'max-plus')
        self.assertAlmostEqual(standard - plus, 0.072, delta=0.001)
        # Both modes retain the same above-threshold content, including the
        # beginning and ending. Only silence is additionally removed here.
        self.assertEqual(standard_pcm[:48000 * 2], plus_pcm[:48000 * 2])
        self.assertEqual(standard_pcm[-48000 * 2:], plus_pcm[-48000 * 2:])
        quiet = self.fixture(quiet=0.012)
        seconds, _ = self.render(quiet, 'max-plus')
        self.assertAlmostEqual(seconds, 12, delta=0.015)
        short = self.fixture(gap=0.06)
        standard, _ = self.render(short)
        plus, _ = self.render(short, 'max-plus')
        self.assertAlmostEqual(standard, 10.06, delta=0.001)
        self.assertLess(plus, standard - 0.02)

    def test_runtime_options_validation_and_cycle_order(self):
        command = self.start_ipc(self.fixture())

        def graph():
            filters = command('get_property', 'af')
            return next((f['params']['graph'] for f in filters
                         if f.get('label') == 'dusky_silence'), None)

        self.wait_for(lambda: graph() is not None)
        original = graph()
        command('set_property', 'script-opts', 'dusky_silence-level=invalid')
        self.wait_for(lambda: 'Unknown silence level' in
                      (self.folder / 'mpv.log').read_text(encoding='utf-8'))
        self.assertEqual(graph(), original)
        filters = command('get_property', 'af')
        for entry in filters:
            if entry.get('label') == 'dusky_silence':
                entry['params']['fix-pts'] = 'yes'
        command('set_property', 'af', filters)
        command('set_property', 'script-opts', 'dusky_silence-level=max')
        self.wait_for(lambda: any(f.get('label') == 'dusky_silence' and
                                 f['params'].get('fix-pts') == 'no'
                                 for f in command('get_property', 'af')))
        for expected in [0, None, 0.648, 0.552, 0.072]:
            command('script-binding', 'dusky_silence/cycle')
            target = None if expected is None else f'stop_duration={expected:.9f}'
            def matches():
                current = graph()
                return current is None if target is None else current is not None and target in current
            self.wait_for(matches)

    def test_timestamps_silent_media_and_asymmetric_stereo(self):
        source = self.fixture(tail=3)
        shifted = self.folder / 'offset.ts'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(source), '-c:a', 'aac',
                        '-output_ts_offset', '100', str(shifted)], check=True)
        for rebase in ['yes', 'no']:
            seconds, pcm = self.render(shifted, extra=[f'--rebase-start-time={rebase}'])
            self.assertAlmostEqual(seconds, 13.096, delta=0.075)
            tail_bytes = round(48000 * 2 * 2.8)
            self.assertEqual(pcm[-tail_bytes:], bytes(tail_bytes))
        # One active channel must prevent stereo trimming; compare byte for
        # byte with bypass, including quiet material and the channel layout.
        asymmetric = self.folder / 'one-channel.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.fixture(quiet=0.012)),
                        '-af', 'pan=stereo|c0=c0|c1=0*c0', str(asymmetric)], check=True)
        for level in ['max', 'max-plus']:
            seconds, pcm = self.render(asymmetric, level)
            _, reference = self.render(asymmetric, extra=[
                '--script-opts-append=dusky_silence-enabled=no'])
            self.assertAlmostEqual(seconds, 12, delta=0.001)
            self.assertEqual(pcm, reference)
        silent = self.folder / 'silent.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'anullsrc=r=48000:cl=mono', '-t', '12', str(silent)], check=True)
        for level in ['max', 'max-plus']:
            seconds, pcm = self.render(silent, level)
            # stop_periods alone preserves all-silent input; do not add start
            # trimming that would alter the accepted modes or remove onsets.
            self.assertAlmostEqual(seconds, 12, delta=0.001)
            self.assertEqual(pcm, bytes(len(pcm)))

    def test_track_selection_and_passthrough(self):
        first = self.fixture(rate=48000)
        second = self.fixture(rate=44100)
        multi = self.folder / 'multi.mka'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(first), '-i', str(second),
                        '-map', '0:a', '-map', '1:a', '-c:a', 'copy', str(multi)], check=True)
        command = self.start_ipc(multi, ['--aid=2'])

        def has_rate(rate):
            filters = command('get_property', 'af')
            return any(f.get('label') == 'dusky_silence' and
                       f'window={1152 / rate:.9f}' in f['params']['graph'] for f in filters)

        self.wait_for(lambda: has_rate(44100))
        command('set_property', 'aid', 1)
        self.wait_for(lambda: has_rate(48000))
        encoded = self.folder / 'passthrough.ac3'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(first), '-c:a', 'ac3',
                        str(encoded)], check=True)
        command('set_property', 'options/audio-spdif', 'ac3')
        command('loadfile', str(encoded), 'replace')
        self.wait_for(lambda: command('get_property', 'path') == str(encoded) and
                      command('get_property', 'audio-codec-name') == 'ac3')
        self.assertFalse(any(f.get('label') == 'dusky_silence'
                             for f in command('get_property', 'af')))


if __name__ == '__main__':
    unittest.main()
