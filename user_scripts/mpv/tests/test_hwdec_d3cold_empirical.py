#!/usr/bin/env python3
"""Empirical multi-GPU hwdec & NVIDIA D3cold power-state verification suite.

Target: Arch Linux, Hyprland (Wayland), Intel iGPU (card0) + NVIDIA dGPU (card1),
Python 3.15+, mpv 0.41+, FFmpeg.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time


def read_sysfs(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8').strip()
    except OSError:
        return 'unknown'


def discover_gpus() -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """Locate Intel iGPU (0x8086) and NVIDIA dGPU (0x10de) sysfs paths."""
    igpu = None
    dgpu = None
    drm_root = Path('/sys/class/drm')
    for card in sorted(drm_root.glob('card[0-9]*')):
        if '-' in card.name:
            continue
        dev = card / 'device'
        vendor = read_sysfs(dev / 'vendor').lower()
        render_node = None
        drm_sub = dev / 'drm'
        if drm_sub.is_dir():
            for child in sorted(drm_sub.iterdir()):
                if child.name.startswith('renderD'):
                    render_node = f'/dev/dri/{child.name}'
                    break
        info = {
            'card': card.name,
            'dev_path': f'/dev/dri/{card.name}',
            'render_node': render_node or 'unknown',
            'sysfs_dev': str(dev.resolve()),
            'power_state_path': str(dev / 'power_state'),
            'runtime_status_path': str(dev / 'power/runtime_status'),
        }
        if vendor == '0x8086' and igpu is None:
            igpu = info
        elif vendor == '0x10de' and dgpu is None:
            dgpu = info

    # Fallback scan in /sys/bus/pci/devices if NVIDIA DRM card node isn't bound
    if dgpu is None:
        for pci_dev in sorted(Path('/sys/bus/pci/devices').iterdir()):
            if read_sysfs(pci_dev / 'vendor').lower() == '0x10de':
                dgpu = {
                    'card': pci_dev.name,
                    'dev_path': pci_dev.name,
                    'render_node': 'none',
                    'sysfs_dev': str(pci_dev.resolve()),
                    'power_state_path': str(pci_dev / 'power_state'),
                    'runtime_status_path': str(pci_dev / 'power/runtime_status'),
                }
                break

    return igpu, dgpu


class PowerStateMonitor:
    """Polls NVIDIA PCI power_state at 25ms intervals to catch any transient wakeups."""

    def __init__(self, power_state_path: str, runtime_status_path: str):
        self.power_path = Path(power_state_path)
        self.runtime_path = Path(runtime_status_path)
        self.samples: list[tuple[float, str, str]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = 0.0

    def current_state(self) -> tuple[str, str]:
        return read_sysfs(self.power_path), read_sysfs(self.runtime_path)

    def start(self):
        self.samples.clear()
        self._stop.clear()
        self._t0 = time.monotonic()
        p_state, r_state = self.current_state()
        self.samples.append((0.0, p_state, r_state))
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            p_state, r_state = self.current_state()
            elapsed = round(time.monotonic() - self._t0, 3)
            if not self.samples or (self.samples[-1][1], self.samples[-1][2]) != (p_state, r_state):
                self.samples.append((elapsed, p_state, r_state))
            time.sleep(0.025)

    def stop(self) -> list[tuple[float, str, str]]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        p_state, r_state = self.current_state()
        elapsed = round(time.monotonic() - self._t0, 3)
        if not self.samples or (self.samples[-1][1], self.samples[-1][2]) != (p_state, r_state):
            self.samples.append((elapsed, p_state, r_state))
        return list(self.samples)


def has_ffmpeg_encoder(encoder: str) -> bool:
    res = subprocess.run(
        ['ffmpeg', '-hide_banner', '-encoders'],
        capture_output=True, text=True, timeout=10, check=False,
    )
    return encoder in res.stdout


def build_clip(path: Path, codec_args: list[str], duration: int = 3) -> Path:
    cmd = [
        'ffmpeg', '-hide_banner', '-v', 'error', '-y',
        '-f', 'lavfi', '-i', f'testsrc2=size=1280x720:rate=30:duration={duration}',
        *codec_args,
        str(path),
    ]
    subprocess.run(cmd, check=True, timeout=45)
    return path


def wait_for_d3cold(monitor: PowerStateMonitor, timeout: float = 30.0) -> tuple[bool, float, str]:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        p_state, _ = monitor.current_state()
        if p_state == 'D3cold':
            return True, round(time.monotonic() - t0, 2), p_state
        time.sleep(0.25)
    p_state, _ = monitor.current_state()
    return False, round(time.monotonic() - t0, 2), p_state


def run_mpv_probe(
    clip_path: Path,
    config_dir: Path,
    work_dir: Path,
    monitor: PowerStateMonitor,
    extra_args: list[str] = (),
    env_overrides: dict[str, str] | None = None,
) -> dict[str, object]:
    ipc_sock = work_dir / f'ipc-{time.monotonic_ns()}.sock'
    log_path = work_dir / f'mpv-{clip_path.stem}-{time.monotonic_ns()}.log'

    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)

    cmd = [
        'mpv',
        f'--config-dir={config_dir}',
        '--ao=null',
        '--window-minimized=yes',
        '--keep-open=yes',
        '--pause=no',
        '--terminal=no',
        '--msg-level=all=v',
        f'--input-ipc-server={ipc_sock}',
        *extra_args,
        str(clip_path),
    ]

    initial_p, initial_r = monitor.current_state()
    monitor.start()

    with log_path.open('w+', encoding='utf-8') as log_f:
        proc = subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT, env=env)
        try:
            sock = socket.socket(socket.AF_UNIX)
            sock.settimeout(5.0)
            deadline = time.monotonic() + 8.0
            while True:
                try:
                    sock.connect(str(ipc_sock))
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if time.monotonic() > deadline:
                        raise RuntimeError(f'mpv IPC socket timed out for {clip_path.name}')
                    time.sleep(0.02)

            stream = sock.makefile('rwb')
            req_id = 0

            def ipc_get(prop: str):
                nonlocal req_id
                req_id += 1
                stream.write((json.dumps({'command': ['get_property', prop], 'request_id': req_id}) + '\n').encode())
                stream.flush()
                while True:
                    line = stream.readline()
                    if not line:
                        return None
                    msg = json.loads(line)
                    if msg.get('request_id') == req_id:
                        return msg.get('data') if msg.get('error') == 'success' else None

            # Wait until video decoder is active and playback advances past 0.3s
            t_wait = time.monotonic() + 8.0
            hwdec_current = None
            while time.monotonic() < t_wait:
                t_pos = ipc_get('time-pos')
                hwdec_current = ipc_get('hwdec-current')
                if t_pos is not None and float(t_pos) >= 0.4 and hwdec_current is not None:
                    break
                time.sleep(0.05)

            # Let it play a little longer to ensure stable decoding & power telemetry
            time.sleep(0.8)

            hwdec_cfg = ipc_get('hwdec')
            if isinstance(hwdec_cfg, list):
                hwdec_cfg = ','.join(hwdec_cfg)
            hwdec_current = ipc_get('hwdec-current') or 'no'
            hwdec_interop = ipc_get('hwdec-interop') or 'none'
            vaapi_dev = ipc_get('vaapi-device') or 'default'
            v_codec = ipc_get('video-codec') or 'unknown'

            req_id += 1
            stream.write((json.dumps({'command': ['quit'], 'request_id': req_id}) + '\n').encode())
            stream.flush()
            stream.close()
            sock.close()
            proc.wait(timeout=5.0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=3.0)

    transitions = monitor.stop()
    unique_states = []
    for _, p_st, _ in transitions:
        if p_st not in unique_states:
            unique_states.append(p_st)

    return {
        'clip': clip_path.name,
        'codec': v_codec,
        'hwdec_cfg': hwdec_cfg,
        'hwdec_current': hwdec_current,
        'hwdec_interop': hwdec_interop,
        'vaapi_device': vaapi_dev,
        'initial_power': f'{initial_p} ({initial_r})',
        'transitions': transitions,
        'unique_power_states': unique_states,
        'stayed_d3cold': unique_states == ['D3cold'],
        'woke_dgpu': 'D0' in unique_states,
    }


def main() -> int:
    if not os.environ.get('WAYLAND_DISPLAY'):
        print('[ERROR] WAYLAND_DISPLAY is not set. Run this inside your Hyprland session.', file=sys.stderr)
        return 1

    xdg = os.environ.get('XDG_CONFIG_HOME')
    config_dir = (Path(xdg) if xdg else Path.home() / '.config') / 'mpv'
    if not config_dir.is_dir():
        print(f'[ERROR] Deployed mpv config not found at {config_dir}', file=sys.stderr)
        return 1

    igpu, dgpu = discover_gpus()
    print('=' * 84)
    print('MPV MULTI-GPU HARDWARE DECODING & NVIDIA D3COLD EMPIRICAL TEST HARNESS')
    print('=' * 84)
    print(f'Deployed Config : {config_dir}')
    print(f'AQ_DRM_DEVICES  : {os.environ.get("AQ_DRM_DEVICES", "<unset>")}')
    print(f'Intel iGPU      : {igpu}')
    print(f'NVIDIA dGPU     : {dgpu}')
    if not dgpu:
        print('[ERROR] No NVIDIA dGPU (0x10de) detected in sysfs.', file=sys.stderr)
        return 1

    monitor = PowerStateMonitor(dgpu['power_state_path'], dgpu['runtime_status_path'])
    p_now, r_now = monitor.current_state()
    print(f'Initial NVIDIA Power State: {p_now} (runtime_status: {r_now})')
    if p_now != 'D3cold':
        print('[INFO] Waiting up to 15s for NVIDIA dGPU to enter D3cold before starting tests...')
        ok, waited, p_now = wait_for_d3cold(monitor, timeout=15.0)
        print(f'Pre-test state after {waited}s: {p_now}')

    with tempfile.TemporaryDirectory(prefix='mpv-hwdec-d3cold-') as tmp:
        work_dir = Path(tmp)
        print('\n[STEP 1] Generating synthetic video fixtures with FFmpeg...')
        fixtures: list[tuple[str, Path]] = []

        fixtures.append((
            '8-bit H.264 (Primary iGPU supported)',
            build_clip(work_dir / 'h264_8bit.mp4', ['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p']),
        ))
        if has_ffmpeg_encoder('libx265'):
            fixtures.append((
                '8-bit HEVC (Primary iGPU supported)',
                build_clip(work_dir / 'hevc_8bit.mp4', ['-c:v', 'libx265', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p']),
            ))
        if has_ffmpeg_encoder('libsvtav1'):
            fixtures.append((
                '8-bit AV1 (Primary iGPU supported)',
                build_clip(work_dir / 'av1_8bit.mp4', ['-c:v', 'libsvtav1', '-preset', '12', '-pix_fmt', 'yuv420p']),
            ))
        elif has_ffmpeg_encoder('libvpx-vp9'):
            fixtures.append((
                '8-bit VP9 (Primary iGPU supported)',
                build_clip(work_dir / 'vp9_8bit.webm', ['-c:v', 'libvpx-vp9', '-cpu-used', '8', '-pix_fmt', 'yuv420p']),
            ))

        hi10p_clip = build_clip(
            work_dir / 'h264_10bit_hi10p.mkv',
            ['-c:v', 'libx264', '-preset', 'ultrafast', '-profile:v', 'high10', '-pix_fmt', 'yuv420p10le'],
        )

        results = []

        # ── Scenario 1: Primary iGPU Supported Codecs ──
        print('\n[STEP 2] Testing Primary iGPU Supported Codecs (Expect: hwdec-current=vaapi, NVIDIA stays D3cold)...')
        for label, clip in fixtures:
            res = run_mpv_probe(clip, config_dir, work_dir, monitor)
            res['scenario'] = label
            results.append(res)
            print(
                f'  -> {label}: hwdec={res["hwdec_cfg"]} | active={res["hwdec_current"]} '
                f'| vaapi-device={res["vaapi_device"]} | NVIDIA states={res["unique_power_states"]}'
            )

        # ── Scenario 2A: Simulated Primary iGPU VA-API Miss -> Secondary GPU Fallback ──
        print('\n[STEP 3] Testing Primary iGPU VA-API Miss with hwdec=vaapi,nvdec-copy...')
        res_fallback = run_mpv_probe(
            fixtures[0][1],
            config_dir,
            work_dir,
            monitor,
            extra_args=['--hwdec=vaapi,nvdec-copy'],
            env_overrides={'LIBVA_DRIVER_NAME': 'nonexistent_igpu_miss'},
        )
        res_fallback['scenario'] = 'Simulated iGPU Miss (hwdec=vaapi,nvdec-copy)'
        results.append(res_fallback)
        print(
            f'  -> {res_fallback["scenario"]}: active={res_fallback["hwdec_current"]} '
            f'| NVIDIA transition timeline={res_fallback["transitions"]}'
        )

        # Wait for D3cold recovery before Scenario 2B if dGPU woke up
        if res_fallback['woke_dgpu']:
            print('  -> Waiting for NVIDIA dGPU to return to D3cold...')
            ok, secs, final_st = wait_for_d3cold(monitor, timeout=30.0)
            print(f'  -> D3cold recovery: {"PASS" if ok else "TIMEOUT"} ({secs}s, state={final_st})')

        # ── Scenario 2B: Codec Unsupported by Both GPUs (H.264 10-bit High10 -> CPU Software) ──
        print('\n[STEP 4] Testing H.264 10-bit High10 (Unsupported by iGPU & dGPU -> CPU Software Fallback)...')
        res_hi10p = run_mpv_probe(hi10p_clip, config_dir, work_dir, monitor)
        res_hi10p['scenario'] = '10-bit H.264 High10 (CPU Fallback)'
        results.append(res_hi10p)
        print(
            f'  -> {res_hi10p["scenario"]}: hwdec={res_hi10p["hwdec_cfg"]} | active={res_hi10p["hwdec_current"]} '
            f'| NVIDIA transition timeline={res_hi10p["transitions"]}'
        )

        # ── Scenario 3: Clean Return to D3cold After All Tests ──
        print('\n[STEP 5] Verifying Final Return to D3cold After Playback Stops...')
        ok_final, recovery_secs, end_state = wait_for_d3cold(monitor, timeout=30.0)
        print(f'  -> Final NVIDIA power_state: {end_state} (recovered in {recovery_secs}s)')

        # ── Summary Report ──
        print('\n' + '=' * 84)
        print(f'{"SCENARIO":<44} | {"HWDEC-CURRENT":<13} | {"NVIDIA POWER STATES"}')
        print('-' * 84)
        for r in results:
            states_str = ' -> '.join(r['unique_power_states'])
            print(f'{r["scenario"]:<44} | {str(r["hwdec_current"]):<13} | {states_str}')
        print('=' * 84)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())