"""Compare the optional cache with actual MPV playback and excerpt boundaries."""
import array
import argparse
import json
import importlib.util
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
        temporary = tempfile.TemporaryDirectory(dir='/dev/shm')
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
        with tempfile.TemporaryDirectory(dir=Path.home()) as disk:
            destination = Path(disk) / 'uncreated' / 'cache.wav'
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
