#!/usr/bin/env python3
"""Max Plus silence trimming and pitch-preserving speedup to AAC M4A or float32 WAV."""
import argparse
import ctypes
import json
import math
import os
import re
import resource
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

type Mount = tuple[Path, Path, str, frozenset[str]]
_libc = ctypes.CDLL(None, use_errno=True)


def child_setup(max_file_bytes: int | None = None):
    """Set child lifetime and optional file-size limit in our single-threaded runner."""
    parent_pid = os.getpid()

    def setup() -> None:
        if _libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent_pid:
            os._exit(127)
        if max_file_bytes is not None:
            limit = min(max_file_bytes, resource.getrlimit(resource.RLIMIT_FSIZE)[1])
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

    return setup


def measure(command: list[str], log: Path, max_file_bytes: int | None = None) -> dict:
    """Measure one child, including its descendants, without RSS accumulation."""
    started = time.monotonic()
    with log.open('x', encoding='utf-8') as output:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, preexec_fn=child_setup(max_file_bytes))
        try:
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
        except BaseException:
            process.kill()
            process.wait()
            raise
    result = {
        'command': command,
        'wall_seconds': time.monotonic() - started,
        'cpu_seconds': usage.ru_utime + usage.ru_stime,
        'peak_rss_kib': usage.ru_maxrss,
        'returncode': process.returncode,
    }
    if process.returncode:
        raise RuntimeError(f'Command failed ({process.returncode}); see {log}')
    return result


def probe(path: Path) -> dict:
    return json.loads(subprocess.check_output([
        'ffprobe', '-hide_banner', '-v', 'error', '-select_streams', 'a:0',
        '-show_entries', 'stream=codec_name,sample_rate,channels,duration:stream_tags:format=duration,size:format_tags',
        '-of', 'json', str(path),
    ], stdin=subprocess.DEVNULL, text=True, encoding='utf-8', timeout=30))


def mounts() -> list[Mount]:
    def unescape(value: str) -> str:
        return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)

    entries = []
    for line in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines():
        before, after = line.split(' - ', 1)
        fields = before.split()
        tail = after.split()
        options = frozenset(fields[5].split(',') + tail[2].split(','))
        entries.append((Path(unescape(fields[3])), Path(unescape(fields[4])), tail[0], options))
    return entries


def available_memory(entries: list[Mount]) -> int:
    fields = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text(encoding='utf-8').splitlines())
    available = int(fields['MemAvailable'].split()[0]) * 1024
    membership = next(Path(line[3:]) for line in Path('/proc/self/cgroup').read_text(encoding='utf-8').splitlines()
                      if line.startswith('0::'))
    # Include ancestor limits: a user/session/container may have less RAM than
    # the host. memory.current includes the group's other processes and tmpfs.
    for root, mount, kind, _ in entries:
        if kind != 'cgroup2' or not membership.is_relative_to(root):
            continue
        directory = mount / membership.relative_to(root)
        while True:
            maximum = directory / 'memory.max'
            if maximum.exists():
                limit = maximum.read_text(encoding='utf-8').strip()
                if limit != 'max':
                    used = int((directory / 'memory.current').read_text(encoding='utf-8'))
                    available = min(available, max(0, int(limit) - used))
            if directory == mount:
                break
            directory = directory.parent
    return available


def swap_is_ram_only() -> bool:
    for line in Path('/proc/swaps').read_text(encoding='utf-8').splitlines()[1:]:
        try:
            device = Path(line.split()[0]).stat().st_rdev
            node = Path(f'/sys/dev/block/{os.major(device)}:{os.minor(device)}')
            if not (node / 'comp_algorithm').exists():
                return False
            backing = node / 'backing_dev'
            if backing.exists() and backing.read_text(encoding='utf-8').strip() != 'none':
                return False
        except OSError:
            return False
    return True


def cache_budget(directory: Path, requested: int) -> tuple[int, str]:
    if not any(directory.is_relative_to(root.resolve())
               for root in (Path('/dev/shm'), Path('/mnt/zram1'))):
        raise ValueError('Output must be under /dev/shm or /mnt/zram1; disk output is disabled')
    entries = mounts()
    _, _, kind, options = max((entry for entry in entries if directory.is_relative_to(entry[1])),
                              key=lambda entry: len(entry[1].parts))
    if kind not in ('tmpfs', 'ramfs'):
        raise ValueError('Output must be on tmpfs or ramfs; disk output is disabled')
    if kind == 'tmpfs' and 'noswap' not in options and not swap_is_ram_only():
        raise ValueError('This tmpfs can use disk-backed swap. Choose a noswap tmpfs; no disk fallback is used')
    requested = min(requested, available_memory(entries) // 4)
    if kind == 'ramfs':
        return requested, kind
    space = os.statvfs(directory)
    return min(requested, max(0, space.f_bavail * space.f_frsize - 128 * 1024**2)), kind


def parse_time(value: str) -> float:
    """Accept seconds, MM:SS or HH:MM:SS without ambiguous clock rollover."""
    try:
        parts = value.strip().split(':')
        if len(parts) == 1:
            result = float(parts[0])
        elif len(parts) in (2, 3) and all(part.isdecimal() for part in parts[:-1]):
            seconds = float(parts[-1])
            if not 0 <= seconds < 60 or len(parts) == 3 and int(parts[-2]) >= 60:
                raise ValueError
            result = seconds + int(parts[-2]) * 60
            if len(parts) == 3:
                result += int(parts[0]) * 3600
        else:
            raise ValueError
        if not math.isfinite(result) or result < 0:
            raise ValueError
        return result
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError('Use nonnegative seconds, MM:SS or HH:MM:SS') from None


def ask_time(prompt: str, default: float | None, valid) -> float | None:
    while True:
        value = input(prompt).strip()
        try:
            result = parse_time(value) if value else default
            if valid(result):
                return result
            print('Time is outside the allowed range. Please try again.')
        except argparse.ArgumentTypeError as error:
            print(error)


def parse_speed(value: str) -> float:
    try:
        speed = float(value)
        if math.isfinite(speed) and speed >= 1:
            return speed
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Speed must be finite and at least 1 (for example 2.5)')


def tempo_filters(speed: float) -> list[str]:
    """Keep each pitch-preserving stage at most 2x to avoid sample skipping."""
    filters = []
    while speed > 2:
        filters.append('atempo=2')
        speed /= 2
    if speed != 1:
        filters.append(f'atempo={speed!r}')
    return filters


def parse_bitrate(value: str) -> int:
    try:
        bitrate = int(value)
        if 0 < bitrate <= sys.maxsize // 1000:
            return bitrate
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('AAC bitrate must be positive integer kbps, representable in bits/s')


def default_output(source: Path, speed: float = 1) -> Path | None:
    candidates = (Path('/dev/shm'), Path('/mnt/zram1'))
    for candidate in candidates:
        if candidate.is_dir() and os.access(candidate, os.W_OK):
            try:
                cache_budget(candidate.resolve(), 4 * 1024**3)
            except (OSError, ValueError):
                continue
            return candidate / f'dusky-audio-{os.getuid()}' / f'{source.stem}-max-plus-{speed:g}x.m4a'
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path, nargs='?', help='New .m4a (AAC) or .wav (float32 PCM) on RAM-backed storage')
    parser.add_argument('--interactive', action='store_true', help='Ask for start time, optional duration and output path')
    parser.add_argument('--start', type=parse_time, default=0, help='Source offset: seconds or HH:MM:SS')
    parser.add_argument('--duration', type=parse_time, help='Optional excerpt length: seconds or HH:MM:SS')
    parser.add_argument('--speed', type=parse_speed, default=1.0,
                        help='Pitch-preserving playback speed, at least 1 (default: 1; e.g. 2.5, 3, 4)')
    parser.add_argument('--bitrate', type=parse_bitrate, metavar='KBPS',
                        help='AAC bitrate in kbps (default: 128 for mono/stereo, 64 per channel otherwise); .m4a only')
    parser.add_argument('--max-cache-gib', type=float, default=4,
                        help='Cache size ceiling in GiB (default: 4); RAM-backed output also uses at most 1/4 available RAM')
    parser.add_argument('--play', action='store_true', help='Open the completed cache in MPV with live trimming disabled')
    args = parser.parse_args()
    if args.output is None and not args.interactive:
        parser.error('Provide an output path or use --interactive')
    source = args.source.expanduser().resolve(strict=True)
    source_stat = source.stat()
    metadata = probe(source)
    if not metadata['streams']:
        parser.error('Source has no audio stream')
    stream = metadata['streams'][0]
    source_duration = float(stream.get('duration') or metadata['format'].get('duration') or 0)
    if not math.isfinite(source_duration) or source_duration <= 0:
        parser.error('Source requires a known, positive duration')
    if args.interactive:
        print(f'Source length: {source_duration:.3f} seconds. Output stays on RAM-backed storage.')
        args.start = ask_time(f'Start [seconds or HH:MM:SS; Enter = {args.start:g}s]: ', args.start,
                              lambda value: value is not None and 0 <= value < source_duration)
        duration_default = 'to the end' if args.duration is None else f'{args.duration:g}s'
        args.duration = ask_time(f'Duration [Enter = {duration_default}]: ', args.duration,
                                 lambda value: value is None or 0 < value <= source_duration - args.start)
        suggested = args.output or default_output(source, args.speed)
        destination = input(f'Output .m4a or .wav path{f" [Enter = {suggested}]" if suggested else ""}: ').strip()
        args.output = Path(destination) if destination else suggested
        if args.output is None:
            parser.error('No usable default RAM folder; provide a tmpfs/ramfs output path')
    output = args.output.expanduser().resolve()
    suffix = output.suffix.lower()
    if suffix not in ('.m4a', '.wav'):
        parser.error('Output must have a .m4a (AAC) or .wav (float32 PCM) extension')
    compressed = suffix == '.m4a'
    if not compressed and args.bitrate is not None:
        parser.error('--bitrate applies only to .m4a output')
    report_path = output.with_suffix('.json')
    log_path = output.with_suffix('.log')
    if any(path.exists() for path in (output, report_path, log_path)):
        parser.error('Output, report or log already exists; choose a new output name')
    if not math.isfinite(args.start) or not 0 <= args.start < source_duration:
        parser.error('--start must be within the source duration')
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        parser.error('--duration must be positive and finite')
    duration = min(args.duration or source_duration, source_duration - args.start)
    rate = int(stream['sample_rate'])
    channels = int(stream['channels'])
    bitrate = (args.bitrate or max(128, channels * 64)) if compressed else None
    # Keep descriptive tags, excluding stale duration, gain and timeline data.
    source_tags = {key.lower(): value for key, value in
                   (metadata['format'].get('tags', {}) | stream.get('tags', {})).items()}
    tags = {key: source_tags[key] for key in (
        'title', 'artist', 'album', 'album_artist', 'composer', 'genre', 'date',
        'track', 'disc', 'comment', 'description', 'copyright',
    ) if key in source_tags}
    if not math.isfinite(args.max_cache_gib) or not 0 < args.max_cache_gib <= sys.maxsize / 1024**3:
        parser.error('--max-cache-gib must be positive, finite and representable')
    ancestor = output.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    try:
        budget, filesystem = cache_budget(ancestor, int(args.max_cache_gib * 1024**3))
    except ValueError as error:
        parser.error(str(error))
    # No silence savings assumed. AAC rate control is approximate: include
    # headroom and packet tables, then enforce the actual budget via RLIMIT_FSIZE.
    tag_bytes = sum(len(key.encode('utf-8')) + len(value.encode('utf-8')) for key, value in tags.items())
    if compressed:
        estimated_bytes = math.ceil((duration / args.speed + 1) *
                                    (bitrate * 1000 / 8 * 1.15 + rate / 1024 * 16)) + 65536 + tag_bytes
    else:
        estimated_bytes = math.ceil((duration + 1) * rate) * channels * 4 + 4096 + tag_bytes
    if estimated_bytes > budget:
        parser.error(f'Untrimmed cache size {"estimate" if compressed else "upper bound"} is '
                     f'{estimated_bytes / 1024**3:.2f} GiB; '
                     f'{filesystem} output budget is {budget / 1024**3:.2f} GiB. '
                     'Choose a shorter excerpt or a RAM mount with more space. No disk fallback is used.')
    # Same RMS/window/all-channel rule as dusky_silence.lua Max Plus. Preserve
    # the last six seconds of this excerpt. Rewrite PTS for contiguous WAV audio;
    # source-timestamp seeking/bookmarks require a separate map (not implemented).
    graph = (
        'aformat=sample_fmts=flt,'
        'silenceremove=stop_periods=-1:stop_duration=0:stop_threshold=0.005:'
        f'stop_mode=all:detection=rms:window={1152 / rate:.9f}:timestamp=copy:'
        f"enable='lt(t,{max(0, duration - 6):.9f})',asetpts=N/SR/TB"
    )
    # Trim in source time first, including the six-second ending guard. At 1x,
    # bypass tempo processing entirely to preserve the existing samples.
    if args.speed != 1:
        graph += ',' + ','.join([*tempo_filters(args.speed), 'asetpts=N/SR/TB'])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.preprocess-', dir=output.parent) as temporary:
        cache = Path(temporary) / f'cache{suffix}'
        encoding = (['-c:a', 'aac', '-b:a', f'{bitrate}k'] if compressed else
                    ['-c:a', 'pcm_f32le', '-rf64', 'auto'])
        tag_options = [*('-metadata', f'{key}={value}') for key, value in tags.items()]
        command = ['ffmpeg', '-nostdin', '-hide_banner', '-nostats', '-n',
                   '-filter_threads', '1', '-threads', '2', '-accurate_seek',
                   '-ss', str(args.start), '-t', str(duration),
                   '-i', str(source), '-map', '0:a:0', '-map_metadata', '-1',
                   '-map_metadata:s:a', '-1', '-map_chapters', '-1',
                   '-af', graph, *encoding, *tag_options, str(cache)]
        measurement = measure(command, log_path, budget)
        cached = probe(cache)
        if not cached['streams']:
            raise RuntimeError('No output audio; use a longer excerpt or lower speed')
        cached_stream = cached['streams'][0]
        cached_rate = int(cached_stream['sample_rate'])
        codec = 'aac' if compressed else 'pcm_f32le'
        if (cached_stream['codec_name'] != codec or int(cached_stream['channels']) != channels
                or (not compressed and cached_rate != rate)):
            raise RuntimeError('Unexpected output audio format; cache was not installed')
        cached_duration = float(cached['format'].get('duration') or 0)
        tolerance = 0.1 + (1024 / cached_rate if compressed else 0)
        if (not math.isfinite(cached_duration) or cached_duration <= 0
                or cached_duration > duration / args.speed + tolerance):
            raise RuntimeError('Unexpected output duration; cache was not installed')
        if cache.stat().st_size >= budget:
            raise RuntimeError('Cache exceeded its size budget; cache was not installed')
        current_stat = source.stat()
        if (current_stat.st_size, current_stat.st_mtime_ns, current_stat.st_ino) != (
                source_stat.st_size, source_stat.st_mtime_ns, source_stat.st_ino):
            raise RuntimeError('Source changed during processing; cache was not installed')
        report = {
            'source': str(source),
            'source_stat': {'size': source_stat.st_size, 'mtime_ns': source_stat.st_mtime_ns},
            'source_start_seconds': args.start,
            'source_duration_seconds': source_duration,
            'excerpt_duration_seconds': duration,
            'cached_duration_seconds': cached_duration,
            'playback_speed': args.speed,
            'removed_seconds': duration - cached_duration,
            'cache_bytes': cache.stat().st_size,
            'cache_budget_bytes': budget,
            'output_size_estimate_bytes': estimated_bytes,
            'untrimmed_upper_bound_bytes': None if compressed else estimated_bytes,
            'output_filesystem': filesystem,
            'output_codec': codec, 'target_bitrate_kbps': bitrate,
            'source_sample_rate': rate, 'sample_rate': cached_rate,
            'channels': channels, 'filter': graph,
            'measurement': measurement,
            'timeline': 'Cache time; original source positions are not mapped',
            'leading_silence': 'Preserved, as in existing MPV Max Plus',
        }
        temporary_report = Path(temporary) / 'report.json'
        with temporary_report.open('x', encoding='utf-8') as report_file:
            json.dump(report, report_file, indent=2)
            report_file.write('\n')
        # Publish complete files without overwriting another invocation's output.
        os.link(temporary_report, report_path)
        try:
            os.link(cache, output)
        except BaseException:
            report_path.unlink()
            raise
    print(json.dumps(report, indent=2))
    if args.play:
        subprocess.run(['mpv', '--cache-on-disk=no', '--speed=1',
                        '--script-opts-append=dusky_silence-enabled=no', str(output)], check=True)


if __name__ == '__main__':
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        main()
    except KeyboardInterrupt:
        print('Preprocessing interrupted; incomplete cache discarded.', file=sys.stderr)
        sys.exit(130)
    except EOFError:
        print('Input ended; preprocessing cancelled.', file=sys.stderr)
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Preprocessing failed: {error}', file=sys.stderr)
        sys.exit(1)
