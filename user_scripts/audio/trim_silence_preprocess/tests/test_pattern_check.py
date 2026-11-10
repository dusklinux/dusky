"""RAM-only detector regressions using decoded fixtures and controlled children."""
import array
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
DETECTOR = ROOT / 'auto_detect_when_wavfrom_has_diff_pattern' / 'pattern_check.py'
SPEC = importlib.util.spec_from_file_location('pattern_check', DETECTOR)
PATTERN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PATTERN
SPEC.loader.exec_module(PATTERN)
VERIFY_SPEC = importlib.util.spec_from_file_location(
    'verify_episodes', DETECTOR.with_name('verify_episodes.py'))
VERIFY = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(VERIFY)


def block(kind, index):
    return PATTERN.BlockMetrics(index * 10, 10, 40 if kind == 'speech' else 0,
                                40 if kind == 'speech' else 0, 8, -20,
                                kind == 'quiet', kind == 'speech', kind == 'music')


class PatternTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/dev/shm')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = self.folder / 'source.wav'

    def audio(self, segments):
        tone = array.array('h', (int(5000 * math.sin(2 * math.pi * 220 * i / 8000))
                                for i in range(8000)))
        speech = tone[:4000] + array.array('h', [0]) * 4000
        # Loud drum-like envelope dips have no absolutely quiet frames.
        drums = array.array('h', (int(x * (0.12 if i % 8000 >= 4000 else 1))
                                 for i, x in enumerate(tone)))
        chunks = {'speech': speech, 'quiet': array.array('h', [0]) * 8000,
                  'music': tone, 'drums': drums}
        with wave.open(str(self.source), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            for kind, duration in segments:
                for _ in range(duration):
                    output.writeframesraw(chunks[kind])
        return self.source

    def detect(self, segments, **kwargs):
        return PATTERN.analyze_episode(self.audio(segments), **kwargs)

    def test_dead_air_then_speech(self):
        result = self.detect([('quiet', 120), ('speech', 660)])
        self.assertEqual(result.show_start_sec, 120)
        self.assertIn('silence-to-speech', result.transition_signature)

    def test_music_then_speech(self):
        result = self.detect([('music', 120), ('speech', 660)])
        self.assertEqual(result.show_start_sec, 120)
        self.assertIn('music-to-speech', result.transition_signature)

    def test_short_bumper_does_not_confirm(self):
        result = self.detect([('quiet', 120), ('speech', 30), ('quiet', 90), ('speech', 660)])
        self.assertEqual(result.show_start_sec, 240)

    def test_drums_do_not_confirm_speech(self):
        result = self.detect([('drums', 700)])
        self.assertIn('No pre-show transition', result.transition_signature)

    def test_immediate_speech(self):
        self.assertEqual(self.detect([('speech', 660)]).show_start_sec, 0)

    def test_short_leading_quiet_onsets(self):
        for onset in (1, 3, 7, 9, 13, 19, 21, 29):
            with self.subTest(onset=onset):
                result = self.detect([('quiet', onset), ('speech', 100)], min_speech_minutes=1)
                self.assertEqual(result.show_start_sec, onset)

    def test_subsecond_onset_in_every_coarse_block_position(self):
        self.audio([('quiet', 100), ('speech', 100)])
        with wave.open(str(self.source), 'rb') as source:
            original = source.readframes(source.getnframes())
        for offset in (0.1, 1.4, 3.7, 7.2, 9.8):
            with wave.open(str(self.source), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframesraw(bytes(int(offset * 8000) * 2))
                output.writeframesraw(original)
            with self.subTest(offset=offset):
                result = PATTERN.analyze_episode(self.source, min_speech_minutes=1)
                self.assertLessEqual(abs(result.show_start_sec - 100 - offset), 1)

    def test_brief_quiet_does_not_move_refinement(self):
        self.audio([('music', 50), ('quiet', 2), ('speech', 50)])
        second, signature = PATTERN.refine_boundary_fine(self.source, 55)
        self.assertNotIn('silence-to-speech', signature)

    def test_backtracking_keeps_show_breaks(self):
        for pause in ('quiet', 'music'):
            kinds = ['quiet'] * 12 + ['speech'] * 20 + [pause] * 6 + ['speech'] * 20
            blocks = [block(kind, i) for i, kind in enumerate(kinds)]
            with self.subTest(pause=pause):
                self.assertEqual(PATTERN._trace_back_to_onset(blocks), 12)

    def test_mid_show_break_with_outro_music_does_not_cut_first_half_of_episode(self):
        # Regression for Critical half-episode truncation bug:
        # 1) Direct backtracking across bumper music + dead air.
        kinds = (
            ['quiet'] * 6
            + ['speech'] * 20
            + ['music'] * 5
            + ['quiet'] * 3
            + ['speech'] * 40
        )
        blocks = [block(kind, i) for i, kind in enumerate(kinds)]
        self.assertEqual(PATTERN._trace_back_to_onset(blocks), 6)
        # 2) Full analyze_episode path where the first half has 50% speech density
        # (< 60% min_speech_pct, so confirmation only locks in during the second half)
        # followed by a 30s music + 30s dead-air mid-show break.
        segments = (
            [('quiet', 30)]
            + [('speech', 10), ('drums', 10)] * 7
            + [('music', 30), ('quiet', 30), ('speech', 120)]
        )
        result = self.detect(segments, min_speech_minutes=2)
        self.assertEqual(result.show_start_sec, 30.0)
        self.assertEqual(result.show_duration_sec, 320.0)

    def test_two_disconnected_bumpers_with_20s_gap_do_not_jump_to_preshow(self):
        # H1 regression: two 20s bumpers separated by a 20s dead-air break must not
        # combine into a 60s confirmation window when backtracking from the real show.
        result = self.detect(
            [('quiet', 20), ('speech', 20), ('quiet', 20), ('speech', 20), ('quiet', 40), ('speech', 80)],
            min_speech_minutes=1,
        )
        self.assertEqual(result.show_start_sec, 120.0)

    def test_end_of_episode_is_never_truncated_by_trailing_silence_or_early_stop(self):
        # Verify show_duration_sec covers the full remainder of the file and preprocess_command
        # never emits an end-trim flag even when the episode has mid-show and trailing silence.
        result = self.detect(
            [('quiet', 30), ('speech', 90), ('quiet', 15), ('speech', 60), ('quiet', 45)],
            min_speech_minutes=1,
        )
        self.assertEqual(result.show_start_sec, 30)
        self.assertEqual(result.total_duration_sec, 240)
        self.assertEqual(result.show_duration_sec, 210)
        self.assertNotIn('--end', result.preprocess_command)
        self.assertNotIn('-to', result.preprocess_command)

    def test_non_qualifying_stinger_after_dead_air_returns_speech_onset(self):
        # 60s dead air + 20s music stinger (<30s music_boundary_sec) + speech at block 8
        # must return block 8 (speech onset), not block 6 (start of the music stinger).
        kinds = ['quiet'] * 6 + ['music'] * 2 + ['speech'] * 40
        blocks = [block(kind, i) for i, kind in enumerate(kinds)]
        self.assertEqual(PATTERN._trace_back_to_onset(blocks), 8)
        # A 10s music block at k == 0 (<30s music_boundary_sec) must not clip block 0.
        opening_stinger = [block('music', 0)] + [block('speech', i) for i in range(1, 20)]
        self.assertEqual(PATTERN._trace_back_to_onset(opening_stinger), 0)

    def test_post_silence_music_bumper_does_not_trigger_priority1_silence_to_speech(self):
        # 25s silence followed by 15s unmodulated music (-31.7 dBFS, below -30 dB) and speech at 40s:
        # Priority 1 must not mistake the music onset at 25s for silence-to-speech.
        quiet_music = array.array('h', (int(1200 * math.sin(2 * math.pi * 220 * i / 8000))
                                        for i in range(8000)))  # ~ -31.7 dBFS
        self.audio([('quiet', 25), ('music', 15), ('speech', 40)])
        with wave.open(str(self.source), 'rb') as src:
            raw = bytearray(src.readframes(src.getnframes()))
        raw[25 * 16000 : 40 * 16000] = quiet_music.tobytes() * 15
        with wave.open(str(self.source), 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(8000)
            out.writeframesraw(raw)
        sec, sig = PATTERN.refine_boundary_fine(self.source, 40.0)
        self.assertEqual(sec, 40.0)
        self.assertIn('music-to-speech', sig)

    def test_single_second_transient_in_leading_quiet_does_not_abort_priority1b(self):
        tone_pop = array.array('h', (int(1200 * math.sin(2 * math.pi * 220 * i / 8000))
                                     for i in range(8000)))  # ~ -31.7 dBFS (> -34 dB) 1s transient
        self.audio([('quiet', 10), ('speech', 70)])
        with wave.open(str(self.source), 'rb') as src:
            raw = bytearray(src.readframes(src.getnframes()))
        raw[2 * 16000 : 3 * 16000] = tone_pop.tobytes()
        with wave.open(str(self.source), 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(8000)
            out.writeframesraw(raw)
        sec, sig = PATTERN.refine_boundary_fine(self.source, 10.0)
        self.assertEqual(sec, 10.0)
        self.assertIn('early speech onset', sig)

    def test_ffprobe_na_stream_duration_falls_back_to_format_duration(self):
        self.audio([('quiet', 10), ('speech', 70)])
        fake_meta = {
            'streams': [{'sample_rate': '8000', 'channels': 1, 'duration': 'N/A'}],
            'format': {'duration': '80.0', 'size': '1280000'},
        }
        with patch.object(PATTERN, 'probe_audio_stream', return_value=fake_meta):
            result = PATTERN.analyze_episode(self.source, min_speech_minutes=1)
        self.assertEqual(result.total_duration_sec, 80.0)
        self.assertEqual(result.show_start_sec, 10.0)

    def test_profile_rejects_impossible_silence_gate_and_zero_speech_pct(self):
        for bad in (
            {'min_abs_silence_pct': 85.0, 'dead_air_silence_pct': 85.0},
            {'min_abs_silence_pct': 90.0, 'dead_air_silence_pct': 85.0},
            {'min_speech_pct': 0.0},
            {'boundary_quiet_sec': 4, 'refine_window_sec': 12},
            {'min_pause_ms': 4000, 'min_pauses': 2},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PATTERN.DetectionProfile(**bad)

    def test_early_speech_onset_gets_high_confidence(self):
        result = self.detect([('quiet', 7), ('speech', 100)], min_speech_minutes=1)
        self.assertEqual(result.show_start_sec, 7)
        self.assertIn('early speech onset', result.transition_signature)
        self.assertEqual(result.confidence_pct, 93.0)

    def test_soft_fade_in_speech_after_dead_air_refines_accurately(self):
        tone_quiet = array.array('h', (int(500 * math.sin(2 * math.pi * 220 * i / 8000))
                                       for i in range(8000)))  # ~ -33.1 dBFS
        tone_mid = array.array('h', (int(1000 * math.sin(2 * math.pi * 220 * i / 8000))
                                     for i in range(8000)))    # ~ -27.1 dBFS
        self.audio([('quiet', 30), ('speech', 70)])
        with wave.open(str(self.source), 'rb') as src:
            raw = bytearray(src.readframes(src.getnframes()))
        # Replace seconds 30 and 31 with a soft 2-second speech fade-in (-33 dB -> -27 dB)
        raw[30 * 16000 : 31 * 16000] = tone_quiet.tobytes()
        raw[31 * 16000 : 32 * 16000] = tone_mid.tobytes()
        with wave.open(str(self.source), 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(8000)
            out.writeframesraw(raw)
        sec, sig = PATTERN.refine_boundary_fine(self.source, 30.0)
        self.assertEqual(sec, 30.0)
        self.assertIn('silence-to-speech', sig)

    def test_opening_bumper_before_20s_dead_air_does_not_hijack_refinement(self):
        result = self.detect([('quiet', 2), ('speech', 5), ('quiet', 23), ('speech', 100)],
                             min_speech_minutes=1)
        self.assertEqual(result.show_start_sec, 30)
        self.assertIn('silence-to-speech', result.transition_signature)

    def test_two_disconnected_bumpers_do_not_confirm_early(self):
        # 20s bumper + 30s dead air + 20s bumper must not confirm a 1-minute (60% = 40s) window
        result = self.detect(
            [('quiet', 20), ('speech', 20), ('quiet', 30), ('speech', 20), ('quiet', 40), ('speech', 80)],
            min_speech_minutes=1,
        )
        self.assertEqual(result.show_start_sec, 130)

    def test_positive_db_and_invalid_threshold_rejected(self):
        for bad in ({'speech_min_db': 44.0}, {'dead_air_max_db': 10.0},
                    {'dead_air_max_db': -30.0, 'speech_min_db': -44.0},
                    {'abs_silence_threshold': 1.5},
                    {'min_speech_pct': 80.0, 'min_speech_or_quiet_pct': 70.0}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PATTERN.DetectionProfile(**bad)

    def test_fine_decoder_failure_is_reported(self):
        failed = subprocess.CompletedProcess([], 1, b'')
        with patch.object(PATTERN.subprocess, 'run', return_value=failed):
            with self.assertRaisesRegex(RuntimeError, 'refinement failed'):
                PATTERN.refine_boundary_fine(self.source, 100)

    def test_refinement_omits_first_pts_zero_on_nonzero_seek(self):
        ok = subprocess.CompletedProcess([], 0, b'')
        with patch.object(PATTERN.subprocess, 'run', return_value=ok) as run_mock:
            PATTERN.refine_boundary_fine(self.source, 20.0)
            cmd_zero = run_mock.call_args.args[0]
            self.assertIn('aresample=async=1:first_pts=0', cmd_zero)
            PATTERN.refine_boundary_fine(self.source, 100.0)
            cmd_seek = run_mock.call_args.args[0]
            self.assertIn('aresample=async=1', cmd_seek)
            self.assertNotIn('aresample=async=1:first_pts=0', cmd_seek)

    def test_analysis_decoder_failure_is_reported(self):
        executable = self.folder / 'ffmpeg'
        self.audio([('speech', 60)])
        for mode in ('empty', 'tiny', 'exact_block'):
            payload = (
                'sys.stdout.buffer.write(bytes(4))\n' if mode == 'tiny' else
                'import array, math\n'
                'tone = array.array("f", (0.15 * math.sin(2 * math.pi * 220 * i / 8000) for i in range(4000)))\n'
                'frame = (tone + array.array("f", [0.0]) * 4000).tobytes()\n'
                'sys.stdout.buffer.write(frame * 60)\n' if mode == 'exact_block' else ''
            )
            executable.write_text(
                f'#!{sys.executable}\nimport sys\n{payload}sys.exit(23)\n',
                encoding='utf-8',
            )
            executable.chmod(0o755)
            with self.subTest(mode=mode), patch.dict(os.environ, PATH=f'{self.folder}:{os.environ["PATH"]}'):
                with self.assertRaisesRegex(RuntimeError, 'analysis failed'):
                    PATTERN.analyze_episode(self.source, min_speech_minutes=1)

    def test_verifier_uses_selected_json_and_reports_mpv_failure(self):
        detections = self.folder / 'detections.json'
        detections.write_text(json.dumps([{
            'filename': 'source.wav', 'filepath': str(self.source),
            'show_start_timestamp': '00:00:01', 'total_duration_sec': 3,
            'transition_signature': 'test',
        }]), encoding='utf-8')
        with patch.object(sys, 'argv', ['verify_episodes.py', str(detections)]), \
             patch('builtins.print'), patch.object(VERIFY.subprocess, 'run') as run:
            VERIFY.main()
            command = run.call_args.args[0]
            self.assertIn('--cache-on-disk=no', command)
            self.assertIn('--script-opts-append=dusky_silence-enabled=no', command)
            self.assertEqual(command[-1], str(self.source))
            self.assertTrue(run.call_args.kwargs['check'])
            run.side_effect = subprocess.CalledProcessError(2, 'mpv')
            with self.assertRaises(SystemExit) as error:
                VERIFY.main()
            self.assertEqual(error.exception.code, 1)

    def test_cli_failure_returns_nonzero(self):
        source = self.folder / 'corrupt.wav'
        source.write_bytes(b'not audio')
        for workers in (1, 2):
            report = self.folder / 'detections.json'
            report.write_text('previous complete report', encoding='utf-8')
            other = self.folder / 'other.wav'
            other.write_bytes(b'also not audio')
            result = subprocess.run([sys.executable, '-B', str(DETECTOR), str(source), str(other),
                                     '--workers', str(workers), '--json-out', str(report)], capture_output=True,
                                    text=True, timeout=10)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(report.read_text(encoding='utf-8'), 'previous complete report')

    def _interrupt_pool(self, signum):
        self.audio([('speech', 2)])
        other = self.folder / 'other.wav'
        other.write_bytes(self.source.read_bytes())
        pid_file = self.folder / 'children'
        executable = self.folder / 'ffmpeg'
        executable.write_text(
            f'#!{sys.executable}\nimport os, time\n'
            f'with open({str(pid_file)!r}, "a") as f:\n'
            '    f.write(f"{os.getpid()}\\n")\n'
            'time.sleep(60)\n', encoding='utf-8')
        executable.chmod(0o755)
        parent = subprocess.Popen([sys.executable, '-B', str(DETECTOR), str(self.source), str(other),
                                   '--workers', '2'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=dict(os.environ, PATH=f'{self.folder}:{os.environ["PATH"]}'),
                                  start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not pid_file.exists() or len(pid_file.read_text(encoding='utf-8').splitlines()) < 2:
                self.assertIsNone(parent.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            parent.send_signal(signum)
            _, error = parent.communicate(timeout=5)
            self.assertEqual(parent.returncode, 130 if signum == signal.SIGTERM else -signum,
                             error.decode())
            for pid in pid_file.read_text(encoding='utf-8').splitlines():
                while True:
                    try:
                        state = Path(f'/proc/{pid}/stat').read_text(encoding='utf-8').split()[2]
                    except (FileNotFoundError, ProcessLookupError):
                        break
                    if state == 'Z':
                        break
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.01)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.communicate(timeout=5)
            try:
                os.killpg(parent.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_cli_interrupt_terminates_pool(self):
        self._interrupt_pool(signal.SIGTERM)

    def test_cli_sigkill_terminates_pool_and_decoders(self):
        self._interrupt_pool(signal.SIGKILL)

    def test_default_report_location(self):
        self.audio([('quiet', 7), ('speech', 100)])
        config = self.folder / 'config'
        result = subprocess.run([sys.executable, '-B', str(DETECTOR), str(self.source),
                                 '--workers', '1', '--min-speech-minutes', '1'],
                                capture_output=True, text=True, timeout=15,
                                env=dict(os.environ, XDG_CONFIG_HOME=str(config)))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = config / 'dusky/settings/audio_detection/detections.json'
        self.assertEqual(json.loads(report.read_text(encoding='utf-8'))[0]['show_start_sec'], 7)
        self.assertEqual(PATTERN.DEFAULT_DETECTIONS_PATH, VERIFY.DEFAULT_DETECTIONS_PATH)
        self.assertFalse(list(report.parent.glob('.detections-*')))
        executable = self.folder / 'mpv'
        executable.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        executable.chmod(0o755)
        verified = subprocess.run([sys.executable, '-B', str(DETECTOR.with_name('verify_episodes.py'))],
                                  capture_output=True, text=True, timeout=10,
                                  env=dict(os.environ, XDG_CONFIG_HOME=str(config),
                                           PATH=f'{self.folder}:{os.environ["PATH"]}'))
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIn('Loaded 1 auto-detected episodes', verified.stdout)

    def test_profiles_inherit_defaults_and_override_independently(self):
        config = self.folder / 'config.toml'
        config.write_text('[defaults]\nmin_speech_minutes = 2.0\nmax_music_pct = 20.0\n'
                          '[profiles.short]\nmin_speech_minutes = 1.0\n'
                          'min_speech_pct = 75.0\n'
                          'min_speech_or_quiet_pct = 75.0\n', encoding='utf-8')
        default = PATTERN.load_profile(config, 'default')
        short = PATTERN.load_profile(config, 'short')
        self.assertEqual(default.min_speech_minutes, 2)
        self.assertEqual(short.min_speech_minutes, 1)
        self.assertEqual(short.max_music_pct, 20)
        self.assertEqual(short.min_speech_pct, 75)
        self.assertEqual(default.min_speech_pct, 60)
        result = self.detect([('quiet', 123), ('speech', 100)], profile=short, profile_name='short')
        self.assertEqual(result.show_start_sec, 123)
        self.assertEqual(result.profile_name, 'short')
        self.assertEqual(result.detection_settings['min_speech_pct'], 75)

    def test_profile_validation_and_unknown_keys(self):
        for settings in ({'min_speech_minutes': 0}, {'min_speech_pct': 101},
                         {'speech_min_db': float('nan')}, {'min_pause_ms': 176},
                         {'min_pause_ms': 175.0}, {'min_pauses': 4, 'max_pauses': 3},
                         {'boundary_quiet_sec': True}, {'refine_window_sec': 5},
                         {'abs_silence_threshold': 0}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                PATTERN.DetectionProfile(**settings)
        config = self.folder / 'config.toml'
        for contents in ('[defaults]\nmin_spech_pct = 80\n',
                         'defaults = "wrong type"\n', '[unknown]\na = 1\n',
                         '[profiles]\nshort = "wrong type"\n'):
            config.write_text(contents, encoding='utf-8')
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                PATTERN.load_profile(config, 'short' if 'short =' in contents else 'default')
        with self.assertRaises(ValueError):
            PATTERN.load_profile(config, 'missing')
        config.unlink()
        self.assertEqual(PATTERN.load_profile(config, 'default'), PATTERN.DEFAULT_PROFILE)
        with self.assertRaises(ValueError):
            PATTERN.load_profile(config, 'default', explicit=True)

    def test_cli_profile_and_explicit_window_override(self):
        self.audio([('quiet', 123), ('speech', 100)])
        config = self.folder / 'config.toml'
        config.write_text('[profiles.short]\nmin_speech_minutes = 2.0\n'
                          'max_music_pct = 10.0\n', encoding='utf-8')
        report = self.folder / 'result.json'
        other = self.folder / 'other.wav'
        other.write_bytes(self.source.read_bytes())
        result = subprocess.run([sys.executable, '-B', str(DETECTOR), str(self.source), str(other),
                                 '--config', str(config), '--profile', 'short', '--workers', '2',
                                 '--min-speech-minutes', '1', '--json-out', str(report)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        detection = json.loads(report.read_text(encoding='utf-8'))[0]
        self.assertEqual(detection['show_start_sec'], 123)
        self.assertEqual(detection['profile_name'], 'short')
        self.assertEqual(detection['detection_settings']['min_speech_minutes'], 1)
        self.assertEqual(detection['detection_settings']['max_music_pct'], 10)

    def test_custom_window_for_subminute_episodes(self):
        profile = PATTERN.DetectionProfile(min_speech_minutes=0.5)
        result = self.detect([('quiet', 10), ('speech', 30)], profile=profile)
        self.assertEqual(result.show_start_sec, 10)
        self.assertEqual(result.show_duration_sec, 30)

    def test_stderr_flood_cannot_block_analysis(self):
        self.audio([('speech', 2)])
        executable = self.folder / 'ffmpeg'
        executable.write_text(f'#!{sys.executable}\nimport sys\n'
                              'sys.stderr.buffer.write(b"e" * 1048576)\n'
                              'sys.exit(23)\n', encoding='utf-8')
        executable.chmod(0o755)
        result = subprocess.run([sys.executable, '-B', str(DETECTOR), str(self.source),
                                 '--workers', '1'], capture_output=True, text=True, timeout=10,
                                env=dict(os.environ, PATH=f'{self.folder}:{os.environ["PATH"]}'))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('analysis failed', result.stderr)

    def test_interrupted_publication_preserves_previous_report(self):
        self.audio([('speech', 100)])
        report = self.folder / 'report.json'
        report.write_text('previous complete report', encoding='utf-8')
        with patch.object(sys, 'argv', ['pattern_check.py', str(self.source), '--workers', '1',
                                       '--min-speech-minutes', '1', '--json-out', str(report)]), \
             patch('builtins.print'), \
             patch.object(PATTERN.Path, 'replace', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                PATTERN.main()
        self.assertEqual(report.read_text(encoding='utf-8'), 'previous complete report')
        self.assertFalse(list(self.folder.glob('.detections-*')))

    def test_nonfinite_pcm_is_rejected(self):
        for value in (float('nan'), float('inf')):
            with wave.open(str(self.source), 'wb') as output:
                output.setformat(wave.WAVE_FORMAT_IEEE_FLOAT)
                output.setnchannels(1)
                output.setsampwidth(4)
                output.setframerate(8000)
                output.writeframes(array.array('f', [value]) * 80000)
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, 'non-finite PCM'):
                    PATTERN.analyze_episode(self.source)
                with self.assertRaisesRegex(RuntimeError, 'non-finite PCM'):
                    PATTERN.refine_boundary_fine(self.source, 0)

    def test_invalid_scan_parameters(self):
        for kwargs in ({'max_scan_hours': math.nan}, {'max_scan_hours': -1},
                       {'min_speech_minutes': 0}, {'min_speech_minutes': math.inf},
                       {'sample_rate': 0}, {'block_sec': .01}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PATTERN.analyze_episode(self.source, **kwargs)

    def test_short_reads_fill_reusable_buffer(self):
        class ShortReader(io.BytesIO):
            def readinto(self, target):
                return super().readinto(target[:3])
        buffer = bytearray(16)
        self.assertEqual(PATTERN.read_full_into(ShortReader(b'abcdefgh'), memoryview(buffer)), 8)
        self.assertEqual(buffer[:8], b'abcdefgh')

    def test_unicode_columns_and_grapheme_truncation(self):
        for text, width in [('字', 2), ('e\u0301', 1), ('👩\u200d💻', 2), ('🏳\u200d🌈', 2), ('🇮🇳', 2), ('क\u094d\u200dष', 1)]:
            self.assertEqual(PATTERN.display_width(text), width)
            self.assertEqual(PATTERN.display_width(PATTERN.format_cell(text * 5, 8)), 8)
        for width in range(4):
            self.assertEqual(PATTERN.display_width(PATTERN.format_cell('abcdef', width)), width)

    def test_ffmpeg_parent_death(self):
        # Kill only our disposable parent; the child's installed prctl must act.
        for module_path in (DETECTOR, ROOT / 'preprocess.py'):
            child_pid = None
            code = (
                'import importlib.util, subprocess, sys, time; '
                f's=importlib.util.spec_from_file_location("helper", {str(module_path)!r}); '
                'm=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); '
                'p=subprocess.Popen(["sleep", "60"], preexec_fn=m.child_setup()); '
                'print(p.pid, flush=True); time.sleep(60)'
            )
            parent = subprocess.Popen([sys.executable, '-B', '-c', code], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
            try:
                child_pid = int(parent.stdout.readline())
                parent.kill()
                parent.communicate(timeout=5)
                deadline = time.monotonic() + 5
                while True:
                    status = Path(f'/proc/{child_pid}/stat')
                    try:
                        state = status.read_text(encoding='utf-8').split()[2]
                    except (FileNotFoundError, ProcessLookupError):
                        break
                    if state == 'Z':
                        break
                    self.assertLess(time.monotonic(), deadline, 'orphaned child survived')
                    time.sleep(.01)
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.communicate(timeout=5)
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
