"""Compare the optional cache with actual MPV playback and excerpt boundaries."""
import array
import argparse
import json
import importlib.util
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MPV_ROOT = ROOT.parents[1] / 'mpv'
SPEC = importlib.util.spec_from_file_location('preprocess', ROOT / 'preprocess.py')
PREPROCESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPROCESS)


class PreprocessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ.get('PREPROCESS_TEST_TMPDIR', '/dev/shm'))
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = self.folder / 'source.wav'
        # Stereo sound, a three-second pause, and a protected five-second ending.
        with wave.open(str(self.source), 'wb') as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(48000)
            for second in range(20):
                value = 0 if 5 <= second < 8 or second >= 15 else 4000
                output.writeframes(array.array('h', [value, value]) * 48000)

    def preprocess(self, destination, *extra):
        return subprocess.run([
            sys.executable, '-B', str(ROOT / 'preprocess.py'), str(self.source),
            str(destination), *extra,
        ], capture_output=True, text=True, timeout=20)

    def render(self, source, name, *extra):
        output = self.folder / name
        result = subprocess.run([
            'mpv', '--no-config', '--vo=null', '--ao=pcm', '--untimed',
            '--load-scripts=no', '--audio-format=float', '--ao-pcm-waveheader=no',
            f'--ao-pcm-file={output}', *extra, str(source),
        ], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return output.read_bytes()

    def test_cache_matches_live_filter_and_keeps_ending(self):
        cache = self.folder / 'cache.wav'
        result = self.preprocess(cache)
        self.assertEqual(result.returncode, 0, result.stderr)
        live = self.render(self.source, 'live.pcm',
                           f'--script={MPV_ROOT}/setup/assets/scripts/dusky_silence.lua',
                           '--script-opts=dusky_silence-level=max-plus')
        cached = self.render(cache, 'cached.pcm')
        self.assertEqual(live, cached)
        self.assertEqual(cached[-5 * 48000 * 2 * 4:], bytes(5 * 48000 * 2 * 4))

    def write_tone(self, rate=48000, channels=2):
        tone = array.array('h', (int(8000 * math.sin(2 * math.pi * 440 * n / rate))
                                 for n in range(rate)))
        block = array.array('h', (sample for sample in tone for _ in range(channels)))
        with wave.open(str(self.source), 'wb') as output:
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(rate)
            for second in range(20):
                output.writeframes(bytes(rate * channels * 2)
                                   if 5 <= second < 8 or second >= 15 else block)

    def test_aac_speed_pitch_size_and_decode(self):
        self.write_tone()
        baseline = self.folder / 'baseline.wav'
        result = self.preprocess(baseline)
        self.assertEqual(result.returncode, 0, result.stderr)
        trimmed_duration = json.loads(result.stdout)['cached_duration_seconds']
        for speed, bitrate in ((1, 128), (2.5, 128), (3, 192), (4, 96), (16, 128)):
            with self.subTest(speed=speed, bitrate=bitrate):
                cache = self.folder / f'aac-{speed}.m4a'
                result = self.preprocess(cache, '--speed', str(speed), '--bitrate', str(bitrate))
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report['output_codec'], 'aac')
                self.assertEqual(report['target_bitrate_kbps'], bitrate)
                self.assertAlmostEqual(report['cached_duration_seconds'], trimmed_duration / speed, delta=.12)
                self.assertLess(report['cache_bytes'], baseline.stat().st_size / 10)
                decoded = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(cache),
                                          '-f', 'f32le', '-'], capture_output=True, check=True, timeout=20)
                samples = array.array('f')
                samples.frombytes(decoded.stdout)
                if sys.byteorder != 'little':
                    samples.byteswap()
                segment = samples[1920:19200:2]  # First tone, 20--200 ms at 48 kHz.
                crossings = sum(a <= 0 < b for a, b in zip(segment, segment[1:]))
                self.assertAlmostEqual(crossings * 48000 / len(segment), 440, delta=8)
                tail_frames = int(48000 * (5 / speed - .15))
                self.assertLess(max(abs(value) for value in samples[-tail_frames * 2:]), .001)

    def test_aac_rates_channels_and_automatic_resampling(self):
        for rate, channels in ((16000, 1), (44100, 2), (48000, 6), (192000, 2)):
            with self.subTest(rate=rate, channels=channels):
                self.write_tone(rate, channels)
                cache = self.folder / f'aac-{rate}-{channels}.m4a'
                result = self.preprocess(cache, '--speed', '2.5')
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report['source_sample_rate'], rate)
                self.assertEqual(report['channels'], channels)
                self.assertEqual(report['target_bitrate_kbps'], max(128, channels * 64))
                self.assertEqual(report['sample_rate'], rate if rate <= 96000 else 96000)
                subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(cache),
                                '-f', 'null', '-'], check=True, timeout=20)

    def test_descriptive_metadata_preserved_chapters_and_stale_tags_removed(self):
        self.write_tone()
        metadata = self.folder / 'tags.txt'
        metadata.write_text(';FFMETADATA1\ntitle=A podcast\nartist=A speaker\nalbum=A series\n'
                            'REPLAYGAIN_TRACK_GAIN=10 dB\n'
                            '[CHAPTER]\nTIMEBASE=1/1000\nSTART=5000\nEND=10000\ntitle=Old chapter\n',
                            encoding='utf-8')
        tagged = self.folder / 'tagged.mka'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.source), '-i', str(metadata),
                        '-map', '0:a:0', '-map_metadata', '1', '-map_chapters', '1',
                        '-c:a', 'copy', str(tagged)], check=True, timeout=20)
        self.source = tagged
        for suffix in ('m4a', 'wav'):
            with self.subTest(suffix=suffix):
                cache = self.folder / f'metadata-{suffix}.{suffix}'
                result = self.preprocess(cache, '--start', '2', '--speed', '3')
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(subprocess.check_output([
                    'ffprobe', '-v', 'error', '-show_format', '-show_streams', '-show_chapters',
                    '-of', 'json', str(cache)], text=True, timeout=20))
                tags = {key.lower(): value for key, value in data['format']['tags'].items()}
                self.assertEqual(tags['title'], 'A podcast')
                self.assertEqual(tags['artist'], 'A speaker')
                self.assertEqual(tags['album'], 'A series')
                self.assertNotIn('replaygain_track_gain', tags)
                self.assertEqual(data['chapters'], [])
                self.assertEqual(len(data['streams']), 1)

    def test_aac_fits_budget_that_rejects_wav(self):
        self.write_tone()
        for suffix, code in (('m4a', 0), ('wav', 2)):
            with self.subTest(suffix=suffix):
                cache = self.folder / f'budget.{suffix}'
                result = self.preprocess(cache, '--max-cache-gib', '.001')
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(cache.exists(), code == 0)

    def test_runtime_file_limit_discards_partial_output(self):
        self.write_tone()
        cache = self.folder / 'limited.m4a'
        measure = PREPROCESS.measure
        partial = None
        limit = 16384

        def exceed_budget(command, log, budget):
            nonlocal partial
            partial = Path(command[-1])
            self.assertEqual(budget, 512 * 1024)
            # Force the real encoder to hit its byte limit after passing preflight.
            try:
                return measure(command, log, limit)
            finally:
                self.assertLessEqual(partial.stat().st_size, limit)

        with patch.object(PREPROCESS, 'measure', side_effect=exceed_budget), \
             patch.object(PREPROCESS, 'cache_budget', return_value=(512 * 1024, 'tmpfs')), \
             patch.object(sys, 'argv', ['preprocess.py', str(self.source), str(cache)]):
            with self.assertRaisesRegex(RuntimeError, 'Command failed'):
                PREPROCESS.main()
        self.assertIsNotNone(partial)
        self.assertFalse(cache.exists())
        self.assertFalse(cache.with_suffix('.json').exists())
        self.assertFalse(list(self.folder.glob('.preprocess-*')))

    def test_bitrate_and_extension_validation(self):
        for suffix, extra in (('mp3', []), ('wav', ['--bitrate', '128']),
                              ('m4a', ['--bitrate', '0']), ('m4a', ['--bitrate', 'nan']),
                              ('m4a', ['--bitrate', '-1']), ('m4a', ['--bitrate', '128k'])):
            with self.subTest(suffix=suffix, extra=extra):
                cache = self.folder / f'invalid.{suffix}'
                result = self.preprocess(cache, *extra)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(cache.exists())
                self.assertFalse(cache.with_suffix('.log').exists())

    def test_default_name_includes_speed_and_uses_aac(self):
        output = PREPROCESS.default_output(self.source, 2.5)
        self.assertIsNotNone(output)
        self.assertEqual(output.name, 'source-max-plus-2.5x.m4a')

    def test_play_starts_at_normal_speed_with_live_trimming_disabled(self):
        cache = self.folder / 'play.m4a'
        called = self.folder / 'mpv-args.json'
        binary = self.folder / 'bin'
        binary.mkdir()
        mpv = binary / 'mpv'
        mpv.write_text(f'#!{sys.executable}\nimport json, sys\n'
                       f'with open({str(called)!r}, "w") as f:\n'
                       '    json.dump(sys.argv[1:], f)\n', encoding='utf-8')
        mpv.chmod(0o755)
        with patch.dict(os.environ, PATH=f'{binary}:{os.environ["PATH"]}'):
            result = self.preprocess(cache, '--speed', '2.5', '--play')
        self.assertEqual(result.returncode, 0, result.stderr)
        options = json.loads(called.read_text(encoding='utf-8'))
        self.assertIn('--speed=1', options)
        self.assertIn('--script-opts-append=dusky_silence-enabled=no', options)
        self.assertEqual(options[-1], str(cache))

    def test_speed_preserves_pitch_channels_rate_and_ending(self):
        for rate, channels, speed in ((16000, 1, 2.5), (44100, 2, 3),
                                      (48000, 6, 4), (96000, 2, 8),
                                      (192000, 1, 16)):
            with self.subTest(rate=rate, channels=channels, speed=speed):
                tone = array.array('h', (int(8000 * math.sin(2 * math.pi * 440 * n / rate))
                                         for n in range(rate)))
                block = array.array('h', (sample for sample in tone for _ in range(channels)))
                silence = bytes(rate * channels * 2)
                with wave.open(str(self.source), 'wb') as output:
                    output.setnchannels(channels)
                    output.setsampwidth(2)
                    output.setframerate(rate)
                    for second in range(20):
                        output.writeframes(silence if 5 <= second < 8 or second >= 15 else block)
                baseline = self.folder / f'baseline-{rate}.wav'
                normal = self.preprocess(baseline)
                self.assertEqual(normal.returncode, 0, normal.stderr)
                cache = self.folder / f'speed-{rate}.wav'
                result = self.preprocess(cache, '--speed', str(speed))
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report['playback_speed'], speed)
                self.assertEqual(report['sample_rate'], rate)
                self.assertEqual(report['channels'], channels)
                self.assertAlmostEqual(report['cached_duration_seconds'],
                                       json.loads(normal.stdout)['cached_duration_seconds'] / speed,
                                       delta=0.1)
                self.assertLess(report['cache_bytes'], json.loads(normal.stdout)['cache_bytes'])
                decoded = subprocess.run([
                    'ffmpeg', '-v', 'error', '-i', str(cache), '-f', 'f32le', '-c:a', 'pcm_f32le', '-',
                ], capture_output=True, timeout=20, check=True).stdout
                samples = array.array('f')
                samples.frombytes(decoded)
                if sys.byteorder != 'little':
                    samples.byteswap()
                segment = samples[int(rate * .02) * channels:int(rate * .20) * channels:channels]
                crossings = sum(a <= 0 < b for a, b in zip(segment, segment[1:]))
                self.assertAlmostEqual(crossings * rate / len(segment), 440, delta=8)
                tail_frames = int(rate * (5 / speed - .1))
                self.assertLess(max(abs(value) for value in samples[-tail_frames * channels:]), 1e-6)

    def test_speed_runs_after_trimming_and_respects_excerpt(self):
        baseline = self.folder / 'trim-first.wav'
        result = self.preprocess(baseline, '--start', '2', '--duration', '16')
        self.assertEqual(result.returncode, 0, result.stderr)
        for speed in (1, 1.001, 2, 2.5, 3, 4, 10):
            with self.subTest(speed=speed):
                cache = self.folder / f'trim-speed-{speed}.wav'
                result = self.preprocess(cache, '--start', '2', '--duration', '16', '--speed', str(speed))
                self.assertEqual(result.returncode, 0, result.stderr)
                if speed == 1:
                    self.assertEqual(cache.read_bytes(), baseline.read_bytes())
                else:
                    # Independent two-step rendering must match the single-pass pipeline.
                    expected = self.folder / f'expected-{speed}.wav'
                    factors = []
                    remaining = speed
                    while remaining > 2:
                        factors.append('atempo=2')
                        remaining /= 2
                    factors.append(f'atempo={remaining!r}')
                    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(baseline),
                                    '-af', ','.join(factors) + ',asetpts=N/SR/TB',
                                    '-c:a', 'pcm_f32le', str(expected)], check=True, timeout=20)
                    with wave.open(str(cache), 'rb') as actual, wave.open(str(expected), 'rb') as reference:
                        self.assertEqual(actual.readframes(actual.getnframes()),
                                         reference.readframes(reference.getnframes()))
                report = json.loads(result.stdout)
                self.assertEqual(report['source_start_seconds'], 2)
                self.assertEqual(report['excerpt_duration_seconds'], 16)
                self.assertEqual(report['removed_seconds'], 16 - report['cached_duration_seconds'])

    def test_invalid_speed_rejected_before_processing(self):
        for speed in ('0', '-1', '0.5', 'nan', 'inf', '-inf', '1e999', 'hello'):
            with self.subTest(speed=speed):
                cache = self.folder / 'invalid-speed.wav'
                result = self.preprocess(cache, f'--speed={speed}')
                self.assertEqual(result.returncode, 2)
                self.assertIn('Speed must be finite and at least 1', result.stderr)
                self.assertFalse(cache.exists())
                self.assertFalse(cache.with_suffix('.log').exists())

    def test_speed_too_short_to_render_fails_cleanly(self):
        cache = self.folder / 'too-short.wav'
        result = self.preprocess(cache, '--duration', '0.000001', '--speed', '16')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(cache.exists())
        self.assertFalse(cache.with_suffix('.json').exists())
        self.assertFalse(list(self.folder.glob('.preprocess-*')))

    def test_duration_limits_input_and_short_clip_is_preserved(self):
        cache = self.folder / 'excerpt.wav'
        result = self.preprocess(cache, '--start', '11', '--duration', '3')
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(result.stdout)
        self.assertEqual(metadata['cached_duration_seconds'], 3)
        cached = self.render(cache, 'excerpt.pcm')
        source = self.render(self.source, 'original.pcm', '--start=11', '--length=3')
        self.assertEqual(cached, source)

    def test_existing_output_is_preserved(self):
        cache = self.folder / 'existing.wav'
        cache.write_bytes(b'keep existing output')
        result = self.preprocess(cache)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(cache.read_bytes(), b'keep existing output')

    def test_rates_channels_and_quiet_one_channel(self):
        for rate, channels in ((16000, 1), (44100, 2), (48000, 6), (96000, 2), (192000, 1)):
            with self.subTest(rate=rate, channels=channels):
                with wave.open(str(self.source), 'wb') as output:
                    output.setnchannels(channels)
                    output.setsampwidth(2)
                    output.setframerate(rate)
                    for second in range(20):
                        # Only one quiet but above-threshold channel is active.
                        value = 0 if 5 <= second < 8 or second >= 15 else 400
                        output.writeframes(array.array('h', [value] + [0] * (channels - 1)) * rate)
                cache = self.folder / f'cache-{rate}-{channels}.wav'
                result = self.preprocess(cache)
                self.assertEqual(result.returncode, 0, result.stderr)
                live = self.render(self.source, 'live.pcm',
                                   f'--script={MPV_ROOT}/setup/assets/scripts/dusky_silence.lua',
                                   '--script-opts=dusky_silence-level=max-plus',
                                   '--audio-channels=auto')
                cached = self.render(cache, 'cached.pcm', '--audio-channels=auto')
                self.assertEqual(live, cached)

    def test_silent_and_leading_silence_are_preserved(self):
        for leading in (True, False):
            with self.subTest(leading=leading):
                with wave.open(str(self.source), 'wb') as output:
                    output.setnchannels(2)
                    output.setsampwidth(2)
                    output.setframerate(48000)
                    for second in range(20):
                        value = 4000 if leading and second >= 10 else 0
                        output.writeframes(array.array('h', [value, value]) * 48000)
                cache = self.folder / f'silent-{leading}.wav'
                result = self.preprocess(cache)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['cached_duration_seconds'], 20)
                cached = self.render(cache, 'cached.pcm')
                source = self.render(self.source, 'original.pcm')
                self.assertEqual(cached, source)

    def test_budget_rejects_before_processing(self):
        cache = self.folder / 'small-budget.wav'
        result = self.preprocess(cache, '--max-cache-gib', '0.001')
        self.assertEqual(result.returncode, 2)
        self.assertIn('output budget', result.stderr)
        self.assertFalse(cache.exists())
        self.assertFalse(cache.with_suffix('.log').exists())

    def test_low_memory_tmpfs_rejects_large_cache(self):
        # Exercise the real CLI path with modeled available memory; no artificial
        # load or global memory settings are applied to this machine.
        cache = self.folder / 'low-memory.wav'
        entries = [(Path('/'), self.folder, 'tmpfs', frozenset({'noswap'}))]
        with patch.object(PREPROCESS, 'mounts', return_value=entries), \
             patch.object(PREPROCESS, 'available_memory', return_value=16 * 1024**2), \
             patch.object(sys, 'argv', ['preprocess.py', str(self.source), str(cache)]):
            with self.assertRaises(SystemExit) as error:
                PREPROCESS.main()
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(cache.exists())

    def test_failed_child_does_not_publish_cache(self):
        cache = self.folder / 'failed.wav'
        with patch.object(PREPROCESS, 'measure', side_effect=RuntimeError('child failure')), \
             patch.object(sys, 'argv', ['preprocess.py', str(self.source), str(cache)]):
            with self.assertRaisesRegex(RuntimeError, 'child failure'):
                PREPROCESS.main()
        self.assertFalse(cache.exists())
        self.assertFalse(cache.with_suffix('.json').exists())
        self.assertFalse(list(self.folder.glob('.preprocess-*')))

    def test_source_changed_does_not_publish_cache(self):
        cache = self.folder / 'changed.wav'
        measure = PREPROCESS.measure

        def change_source(*args):
            result = measure(*args)
            self.source.touch()
            return result

        with patch.object(PREPROCESS, 'measure', side_effect=change_source), \
             patch.object(sys, 'argv', ['preprocess.py', str(self.source), str(cache)]):
            with self.assertRaisesRegex(RuntimeError, 'Source changed'):
                PREPROCESS.main()
        self.assertFalse(cache.exists())
        self.assertFalse(cache.with_suffix('.json').exists())

    def test_corrupt_audio_fails_without_cache(self):
        self.source.write_bytes(b'not an audio file')
        cache = self.folder / 'corrupt.wav'
        result = self.preprocess(cache)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(cache.exists())

    def test_interrupt_kills_child(self):
        child_pid = self.folder / 'child.pid'
        # A controlled child writes its PID, signals the parent during wait4,
        # then blocks. measure must reap it before propagating interruption.
        code = ('import os, signal, time; '
                f'open({str(child_pid)!r}, "w").write(str(os.getpid())); '
                'time.sleep(0.1); os.kill(os.getppid(), signal.SIGINT); time.sleep(60)')
        with self.assertRaises(KeyboardInterrupt):
            PREPROCESS.measure([sys.executable, '-c', code], self.folder / 'interrupt.log')
        with self.assertRaises(ProcessLookupError):
            os.kill(int(child_pid.read_text()), 0)

    def test_concurrent_output_does_not_overwrite(self):
        cache = self.folder / 'concurrent.wav'
        command = [sys.executable, '-B', str(ROOT / 'preprocess.py'), str(self.source), str(cache)]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for _ in range(2)]
        for process in processes:
            process.communicate(timeout=20)
        self.assertEqual(sum(process.returncode == 0 for process in processes), 1)
        self.assertTrue(cache.exists())
        self.assertTrue(cache.with_suffix('.json').exists())
        self.assertFalse(list(self.folder.glob('.preprocess-*')))

    def test_output_created_during_processing_is_preserved(self):
        cache = self.folder / 'raced.wav'
        measure = PREPROCESS.measure

        def create_output(*args):
            result = measure(*args)
            cache.write_bytes(b'other invocation')
            return result

        with patch.object(PREPROCESS, 'measure', side_effect=create_output), \
             patch.object(sys, 'argv', ['preprocess.py', str(self.source), str(cache)]):
            with self.assertRaises(FileExistsError):
                PREPROCESS.main()
        self.assertEqual(cache.read_bytes(), b'other invocation')
        self.assertFalse(cache.with_suffix('.json').exists())

    def test_paths_with_spaces(self):
        cache = self.folder / 'a folder' / 'a cache.wav'
        result = self.preprocess(cache)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(cache.exists())

    def test_sigterm_discards_partial_cache_and_reaps_child(self):
        bin_directory = self.folder / 'bin'
        bin_directory.mkdir()
        child_pid = self.folder / 'ffmpeg.pid'
        ffmpeg = bin_directory / 'ffmpeg'
        ffmpeg.write_text(f'#!{sys.executable}\nimport os, time\n'
                          f'with open({str(child_pid)!r}, "w") as output:\n'
                          '    output.write(str(os.getpid()))\n'
                          'time.sleep(60)\n', encoding='utf-8')
        ffmpeg.chmod(0o755)
        cache = self.folder / 'terminated.wav'
        process = subprocess.Popen([
            sys.executable, '-B', str(ROOT / 'preprocess.py'), str(self.source), str(cache),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(os.environ, PATH=f'{bin_directory}:{os.environ["PATH"]}'))
        try:
            deadline = time.monotonic() + 10
            while not child_pid.exists():
                self.assertIsNone(process.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            process.terminate()
            _, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 130, error.decode())
            self.assertFalse(cache.exists())
            self.assertFalse(cache.with_suffix('.json').exists())
            self.assertFalse(list(self.folder.glob('.preprocess-*')))
            with self.assertRaises(ProcessLookupError):
                os.kill(int(child_pid.read_text()), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)

    def test_time_formats_and_invalid_inputs(self):
        for value, expected in [('8580', 8580), ('2:23:00', 8580), ('143:00', 8580),
                                ('00:01.5', 1.5), ('0', 0)]:
            self.assertEqual(PREPROCESS.parse_time(value), expected)
        for value in ['-1', 'nan', 'inf', '1:60', '1:99:00', '1:2:3:4', 'hello', '']:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                PREPROCESS.parse_time(value)

    def test_interactive_custom_times_and_output(self):
        cache = self.folder / 'interactive.wav'
        result = subprocess.run([
            sys.executable, '-B', str(ROOT / 'preprocess.py'), str(self.source), '--interactive',
        ], input=f'bad\n00:11\n00:03\n{cache}\n', capture_output=True,
            text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(cache.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(report['source_start_seconds'], 11)
        self.assertEqual(report['cached_duration_seconds'], 3)

    def test_interactive_defaults_process_whole_file(self):
        cache = self.folder / 'defaults.wav'
        result = subprocess.run([
            sys.executable, '-B', str(ROOT / 'preprocess.py'), str(self.source),
            str(cache), '--interactive',
        ], input='\n\n\n', capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(cache.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(report['source_start_seconds'], 0)
        self.assertEqual(report['excerpt_duration_seconds'], 20)

    def test_interactive_eof_cancels(self):
        result = subprocess.run([
            sys.executable, '-B', str(ROOT / 'preprocess.py'), str(self.source), '--interactive',
        ], input='', capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 130)
        self.assertNotIn('Traceback', result.stderr)

    def test_disk_output_rejected_before_creating_directory(self):
        destination = Path.home() / f'.preprocess-rejected-{self.folder.name}' / 'cache.wav'
        self.assertFalse(destination.parent.exists())
        result = self.preprocess(destination)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('disk output is disabled', result.stderr)
        self.assertFalse(destination.parent.exists())

    def test_tmpfs_disk_swap_rejected_and_noswap_accepted(self):
        entries = [(Path('/'), self.folder, 'tmpfs', frozenset())]
        with patch.object(PREPROCESS, 'mounts', return_value=entries), \
             patch.object(PREPROCESS, 'swap_is_ram_only', return_value=False):
            with self.assertRaisesRegex(ValueError, 'disk-backed swap'):
                PREPROCESS.cache_budget(self.folder, 1024**3)
            entries[0] = (Path('/'), self.folder, 'tmpfs', frozenset({'noswap'}))
            self.assertGreater(PREPROCESS.cache_budget(self.folder, 1024**3)[0], 0)

    def test_ramfs_does_not_require_swap_or_disk_space(self):
        entries = [(Path('/'), self.folder, 'ramfs', frozenset())]
        with patch.object(PREPROCESS, 'mounts', return_value=entries), \
             patch.object(PREPROCESS, 'available_memory', return_value=1024**3), \
             patch.object(PREPROCESS.os, 'statvfs', side_effect=AssertionError('ramfs has no space ceiling')):
            self.assertEqual(PREPROCESS.cache_budget(self.folder, 4 * 1024**3), (256 * 1024**2, 'ramfs'))

    def test_swap_devices_and_zram_writeback(self):
        header = 'Filename Type Size Used Priority\n'
        device = '/dev/test-swap partition 1024 0 100\n'
        for swap_table, zram, backing, expected in (
            (header, False, 'none', True),
            (header + device, False, 'none', False),
            (header + device, True, 'none', True),
            (header + device, True, '/dev/disk', False),
        ):
            with self.subTest(zram=zram, backing=backing, swap=swap_table), \
                 patch.object(PREPROCESS.Path, 'read_text',
                              side_effect=lambda path, **kw: swap_table if str(path) == '/proc/swaps' else backing,
                              autospec=True), \
                 patch.object(PREPROCESS.Path, 'stat', return_value=SimpleNamespace(st_rdev=os.makedev(253, 0))), \
                 patch.object(PREPROCESS.Path, 'exists',
                              side_effect=lambda path: zram if path.name == 'comp_algorithm' else True,
                              autospec=True):
                self.assertEqual(PREPROCESS.swap_is_ram_only(), expected)

    def test_interactive_uses_discovered_ram_default(self):
        cache = self.folder / 'discovered.wav'
        with patch.object(PREPROCESS, 'default_output', return_value=cache), \
             patch('builtins.input', side_effect=['', '', '']), \
             patch('builtins.print'), \
             patch.object(sys, 'argv', ['preprocess.py', str(self.source), '--interactive']):
            PREPROCESS.main()
        self.assertTrue(cache.exists())
