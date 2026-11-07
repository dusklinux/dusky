#!/usr/bin/env python3
"""Comprehensive stress test for the deployed ~/.config/mpv Dusky Player setup.

Target: Arch Linux, Hyprland (Wayland), Python 3.15+, mpv 0.41+, FFmpeg.
"""
import array
import json
import math
import os
from pathlib import Path
import random
import socket
import subprocess
import tempfile
import time
import unittest
import wave


def get_mpv_config_dir() -> Path:
    xdg = os.environ.get('XDG_CONFIG_HOME')
    base = Path(xdg) if xdg else Path.home() / '.config'
    return base / 'mpv'


def get_rss_kib(pid: int) -> int:
    try:
        status = Path(f'/proc/{pid}/status').read_text(encoding='utf-8')
        for line in status.splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1])
    except OSError:
        pass
    return 0


def get_child_pids(ppid: int) -> list[int]:
    children = []
    for proc_dir in Path('/proc').iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            stat_text = (proc_dir / 'stat').read_text(encoding='utf-8')
            after_comm = stat_text.rpartition(')')[2].split()
            if len(after_comm) > 1 and int(after_comm[1]) == ppid and after_comm[0] != 'Z':
                children.append(int(proc_dir.name))
        except OSError:
            continue
    return children


class DeployedMpvStressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config_dir = get_mpv_config_dir()
        if not cls.config_dir.is_dir():
            raise RuntimeError(f'Deployed config directory not found: {cls.config_dir}')

        cls.tmp = tempfile.TemporaryDirectory(prefix='mpv-deployed-stress-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.folder = Path(cls.tmp.name)

        # 1. Create sine-wave audio fixture with 2s tone, 2s silence, 8s tone, 3s tail (15s total)
        cls.audio_file = cls._make_audio_fixture(cls.folder / 'speech_pause.wav', rate=48000, gap=2.0, tail=3.0)

        # 2. Create 24 small audio files for directory/playlist/ffprobe queue stress
        cls.batch_dir = cls.folder / 'batch_media'
        cls.batch_dir.mkdir()
        cls.batch_files = []
        for idx in range(1, 25):
            path = cls._make_audio_fixture(
                cls.batch_dir / f'episode_{idx:02d}.wav',
                rate=48000,
                gap=0.5,
                tail=0.0,
                tone_a=1.0,
                tone_b=1.0 + (idx % 3) * 0.25,
            )
            cls.batch_files.append(path)

        # 3. Create synthetic video fixture with chapters using ffmpeg lavfi testsrc2
        cls.video_file = cls.folder / 'synthetic_video.mkv'
        chapters_meta = cls.folder / 'chapters.txt'
        chapters_meta.write_text(
            ';FFMETADATA1\n'
            '[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=3000\ntitle=Intro\n'
            '[CHAPTER]\nTIMEBASE=1/1000\nSTART=3000\nEND=9000\ntitle=Main Content\n'
            '[CHAPTER]\nTIMEBASE=1/1000\nSTART=9000\nEND=12000\ntitle=Ending\n',
            encoding='utf-8',
        )
        subprocess.run([
            'ffmpeg', '-hide_banner', '-v', 'error',
            '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=12',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=12',
            '-i', str(chapters_meta),
            '-map_metadata', '2',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '128k',
            str(cls.video_file),
        ], check=True, timeout=30)

    @staticmethod
    def _make_audio_fixture(path: Path, rate=48000, gap=2.0, tail=3.0, tone_a=2.0, tone_b=8.0) -> Path:
        samples = array.array('h')
        for seconds, amplitude in [(tone_a, 0.12), (gap, 0.0), (tone_b, 0.12), (tail, 0.0)]:
            if seconds <= 0:
                continue
            period = array.array('h', (
                int(32767 * amplitude * math.sin(2 * math.pi * 400 * i / rate))
                for i in range(rate // 100)
            ))
            samples.extend(period * round(seconds * 100))
        with wave.open(str(path), 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(samples.tobytes())
        return path

    def start_mpv(self, targets=(), extra_args=(), use_wayland_vo=False):
        ipc_path = self.folder / f'ipc-{time.monotonic_ns()}.sock'
        log_path = self.folder / f'mpv-{time.monotonic_ns()}.log'
        log_file = log_path.open('w+', encoding='utf-8')
        self.addCleanup(log_file.close)

        vo_args = []
        if not use_wayland_vo or not os.environ.get('WAYLAND_DISPLAY'):
            vo_args = ['--vo=null', '--force-window=no']
        else:
            vo_args = ['--window-minimized=yes']

        cmd = [
            'mpv',
            f'--config-dir={self.config_dir}',
            '--ao=null',
            '--terminal=no',
            f'--input-ipc-server={ipc_path}',
            *vo_args,
            *extra_args,
            *(str(t) for t in targets),
        ]
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)

        def cleanup_proc():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

        self.addCleanup(cleanup_proc)

        sock = socket.socket(socket.AF_UNIX)
        self.addCleanup(sock.close)
        sock.settimeout(5)
        deadline = time.monotonic() + 8
        while True:
            try:
                sock.connect(str(ipc_path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() > deadline:
                    log_file.flush()
                    log_file.seek(0)
                    self.fail(f'mpv IPC did not start. Log:\n{log_file.read()}')
                time.sleep(0.02)

        stream = sock.makefile('rwb')
        self.addCleanup(stream.close)
        req_id = 0

        def ipc(*command_args, allow_unavailable=False):
            nonlocal req_id
            req_id += 1
            payload = json.dumps({'command': list(command_args), 'request_id': req_id}).encode('utf-8') + b'\n'
            stream.write(payload)
            stream.flush()
            while True:
                line = stream.readline()
                if not line:
                    raise RuntimeError('EOF on mpv IPC socket')
                msg = json.loads(line)
                if msg.get('request_id') == req_id:
                    err = msg.get('error')
                    if allow_unavailable and err == 'property unavailable':
                        return None
                    self.assertEqual(err, 'success', f'IPC command {command_args} failed: {msg}')
                    return msg.get('data')

        def ipc_get(prop_name: str):
            return ipc('get_property', prop_name, allow_unavailable=True)

        return proc, ipc, ipc_get, log_path

    def wait_until(self, predicate, timeout=8.0, desc='condition'):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            val = predicate()
            if val:
                return val
            time.sleep(0.02)
        self.fail(f'Timed out waiting for {desc}')

    def assert_clean_log(self, log_path: Path):
        content = log_path.read_text(encoding='utf-8')
        self.assertNotIn('Lua error', content, f'Lua error detected in mpv log:\n{content}')
        self.assertNotIn('stack traceback:', content, f'Stack traceback in mpv log:\n{content}')

    def test_01_deployed_bundle_files_present(self):
        required = [
            'mpv.conf',
            'input.conf',
            'script-opts/dusky_player.conf',
            'script-opts/dusky_silence.conf',
            'scripts/dusky_silence.lua',
            'scripts/dusky_thumbnails.lua',
            'scripts/hwdec_topology.lua',
            'scripts/dusky_player/main.lua',
            'scripts/dusky_player/lib/ass.lua',
            'scripts/dusky_player/lib/buttons.lua',
            'scripts/dusky_player/lib/cursor.lua',
            'scripts/dusky_player/lib/fzy.lua',
            'scripts/dusky_player/lib/icons.lua',
            'scripts/dusky_player/lib/menus.lua',
            'scripts/dusky_player/lib/std.lua',
            'scripts/dusky_player/lib/text.lua',
            'scripts/dusky_player/lib/theme.lua',
            'scripts/dusky_player/lib/utils.lua',
            'scripts/dusky_player/lib/waveform.lua',
            'scripts/dusky_player/elements/Menu.lua',
            'scripts/dusky_player/elements/Timeline.lua',
            'scripts/dusky_player/elements/TopBar.lua',
            'scripts/dusky_player/elements/Controls.lua',
            'scripts/dusky_player/elements/Volume.lua',
        ]
        for rel in required:
            target = self.config_dir / rel
            self.assertTrue(target.is_file() and target.stat().st_size > 0, f'Missing deployed file: {target}')

    def test_02_deployed_silence_trimming_all_levels(self):
        for level, blocks in [('mild', 28), ('medium', 24), ('max', 4), ('max-plus', 1)]:
            with self.subTest(level=level):
                out_path = self.folder / f'out-{level}.wav'
                cmd = [
                    'mpv',
                    '--no-config',
                    f'--script={self.config_dir}/scripts/dusky_silence.lua',
                    '--vo=null',
                    '--force-window=no',
                    '--keep-open=no',
                    '--ao=pcm',
                    '--untimed',
                    '--audio-format=s16',
                    f'--ao-pcm-file={out_path}',
                    f'--script-opts=dusky_silence-level={level}',
                    str(self.audio_file),
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                self.assertNotIn('Lua error', res.stdout + res.stderr)
                with wave.open(str(out_path), 'rb') as wav:
                    duration = wav.getnframes() / wav.getframerate()
                    pcm = wav.readframes(wav.getnframes())
                retained = min(2.0, blocks * 1152 / 48000)
                self.assertAlmostEqual(duration, 13.0 + retained, delta=0.05)
                tail_bytes = 48000 * 2 * 3
                self.assertEqual(pcm[-tail_bytes:], bytes(tail_bytes))

    def test_03_rapid_100_seeks_waveform_and_silence_cycling(self):
        proc, ipc, ipc_get, log_path = self.start_mpv(
            targets=[self.audio_file],
            extra_args=['--pause=no', '--script-opts-append=dusky_player-timeline_waveform=yes'],
        )
        self.wait_until(
            lambda: any(f.get('label') == 'dusky_silence' for f in (ipc_get('af') or [])),
            desc='dusky_silence filter attachment',
        )

        # Cycle 20 times total across the 100 seeks (4 full 5-step cycles:
        # max -> max-plus -> off -> mild -> medium -> max) so it lands back on 'max'.
        rng = random.Random(42)
        for i in range(100):
            target_sec = round(rng.uniform(0.0, 14.0), 3)
            mode = 'absolute+exact' if i % 2 == 0 else 'absolute+keyframes'
            ipc('seek', target_sec, mode)
            if i % 5 == 0:
                ipc('script-binding', 'dusky_silence/cycle')
            if i % 25 == 0:
                ipc('script-binding', 'dusky_player/toggle-waveform')

        self.wait_until(
            lambda: any(
                f.get('label') == 'dusky_silence' and f.get('params', {}).get('fix-pts') == 'no'
                for f in (ipc_get('af') or [])
            ),
            desc='dusky_silence filter restored to max after 20 cycles',
        )

        ipc('quit')
        proc.wait(timeout=5)
        self.assertEqual(proc.returncode, 0)
        self.assert_clean_log(log_path)

    def test_04_menu_search_hammering_playlist_and_memory_stability(self):
        proc, ipc, ipc_get, log_path = self.start_mpv(
            targets=self.batch_files,
            extra_args=[
                '--pause=yes',
                '--loop-playlist=inf',
                '--script-opts-append=dusky_player-media_keys_mode=navigate',
            ],
        )
        self.wait_until(
            lambda: ipc_get('playlist-count') == len(self.batch_files) and ipc_get('playlist-pos-1') == 1,
            desc='playlist initialization',
        )

        initial_rss = get_rss_kib(proc.pid)

        # 1. Hammer keybinds menu & search-query-update / search-cancel
        queries = ['seek', 'volume', 'sub', 'audio', 'silence', 'wave', 'fullscreen', 'quit']
        for _ in range(25):
            ipc('script-binding', 'dusky_player/keybinds')
            for q in queries:
                ipc('script-message-to', 'dusky_player', 'menu-action', 'search-query-update', q)
            ipc('script-message-to', 'dusky_player', 'menu-action', 'search-cancel')
            ipc('script-message-to', 'dusky_player', 'close-menu', 'keybinds')

        # 2. Hammer open-file menu & ffprobe metadata queue + playlist wrap-around navigation
        ipc('script-binding', 'dusky_player/open-file')
        time.sleep(0.15)
        ipc('script-message-to', 'dusky_player', 'close-menu', 'open-file')

        # Verify loop-playlist backward wrap from index 1 -> 24 and forward 24 -> 1
        ipc('script-binding', 'dusky_player/first')
        self.wait_until(lambda: ipc_get('playlist-pos-1') == 1, desc='first track')
        ipc('script-binding', 'dusky_player/prev')
        self.wait_until(lambda: ipc_get('playlist-pos-1') == len(self.batch_files), desc='backward wrap to last track')
        ipc('script-binding', 'dusky_player/next')
        self.wait_until(lambda: ipc_get('playlist-pos-1') == 1, desc='forward wrap to first track')

        # Rapid shuffle and UI element toggles
        ipc('script-binding', 'dusky_player/shuffle')
        for _ in range(20):
            ipc('script-binding', 'dusky_player/next')
            ipc('script-binding', 'dusky_player/flash-ui')
        ipc('script-binding', 'dusky_player/toggle-ui')
        ipc('script-binding', 'dusky_player/toggle-ui')

        final_rss = get_rss_kib(proc.pid)
        if initial_rss > 0 and final_rss > 0:
            self.assertLess(final_rss - initial_rss, 65536, f'Excessive RSS growth: {initial_rss} -> {final_rss} KiB')

        ipc('quit')
        proc.wait(timeout=5)
        self.assertEqual(proc.returncode, 0)
        self.assert_clean_log(log_path)

    def test_05_video_playback_hwdec_thumbnails_and_zero_orphan_processes(self):
        proc, ipc, ipc_get, log_path = self.start_mpv(
            targets=[self.video_file],
            extra_args=['--pause=yes'],
            use_wayland_vo=True,
        )
        self.wait_until(lambda: ipc_get('duration') is not None, desc='video duration probe')

        # Verify hwdec property was configured by hwdec_topology.lua and video excludes dusky_silence
        hwdec_val = ipc_get('hwdec')
        if isinstance(hwdec_val, list):
            hwdec_val = ','.join(hwdec_val)
        self.assertIn(hwdec_val, ('vaapi', 'vaapi,nvdec-copy', 'nvdec', 'nvdec,vaapi-copy'))
        af = ipc_get('af') or []
        self.assertFalse(any(f.get('label') == 'dusky_silence' for f in af))

        # Trigger thumbnail generation across timestamps via dusky_thumbnails
        for sec in (1.0, 4.5, 7.2, 10.5):
            ipc('script-message-to', 'dusky_thumbnails', 'thumb', str(sec), '100', '100')
            time.sleep(0.08)
        ipc('script-message-to', 'dusky_thumbnails', 'clear')

        # Exercise chapters menu and playback memory menu
        ipc('script-binding', 'dusky_player/chapters')
        time.sleep(0.05)
        ipc('script-binding', 'dusky_player/clear-history')
        time.sleep(0.05)
        ipc('script-message-to', 'dusky_player', 'close-menu')

        mpv_pid = proc.pid
        ipc('quit')
        proc.wait(timeout=5)
        self.assertEqual(proc.returncode, 0)
        self.assert_clean_log(log_path)

        # Verify zero surviving child processes or leftover thumbnail files for this PID
        time.sleep(0.1)
        surviving = get_child_pids(mpv_pid)
        self.assertEqual(surviving, [], f'Orphaned child processes left behind: {surviving}')
        for leftover in Path('/tmp').glob(f'*dusky-thumbnails*{mpv_pid}*'):
            self.fail(f'Leftover thumbnail artifact not cleaned up: {leftover}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
