#!/usr/bin/env python3
"""Measure sink inputs concurrently; emit index -> yes/no/unknown as JSON.

A running node can carry silence. Brief, read-only per-stream monitor captures
provide actual signal activity without recording or retaining audio files.
"""

lazy import asyncio
import json
lazy import math
import signal
lazy import struct
lazy import subprocess
import sys

WINDOW_SECONDS = 0.4
# Peak threshold of -70 dBFS: ignore numerical noise, retain quiet media.
MIN_PEAK = 10 ** (-70 / 20)


def has_signal(data: bytes) -> bool:
    aligned = memoryview(data)[: len(data) // 4 * 4]
    return any(
        math.isfinite(value) and abs(value) >= MIN_PEAK
        for (value,) in struct.iter_unpack("<f", aligned)
    )


async def measure(stream: dict, monitors: dict) -> tuple[str, str]:
    index = str(stream["index"])
    if stream.get("corked") or stream.get("mute"):
        return index, "no"
    monitor = monitors.get(stream.get("sink"))
    if monitor is None:
        return index, "unknown"
    try:
        process = await asyncio.create_subprocess_exec(
            "parec",
            "--raw",
            "--format=float32le",
            "--rate=48000",
            "--channels=2",
            "--latency-msec=20",
            f"--device={monitor}",
            f"--monitor-stream={index}",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        # One failed monitor must not discard successful measurements elsewhere.
        return index, "unknown"
    # Keep one reader alive when the capture window ends: cancelling communicate
    # would discard buffered samples. Always reap the monitor process.
    reader = asyncio.create_task(process.communicate())
    try:
        await asyncio.wait({reader}, timeout=WINDOW_SECONDS)
    finally:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            async with asyncio.timeout(1):
                data, _ = await asyncio.shield(reader)
        except TimeoutError:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            data, _ = await reader
    if has_signal(data):
        return index, "yes"
    # A failed capture or no samples is inconclusive, not proof of silence.
    if len(data) < 4 or process.returncode not in (0, -signal.SIGTERM):
        return index, "unknown"
    return index, "no"


async def sample(streams: list[dict], monitors: dict) -> dict[str, str]:
    results = await asyncio.gather(*(measure(stream, monitors) for stream in streams))
    return dict(results)


def main() -> int:
    streams = json.load(sys.stdin)
    if not any(not stream.get("corked") and not stream.get("mute") for stream in streams):
        print(json.dumps({str(stream["index"]): "no" for stream in streams}))
        return 0
    try:
        sinks = json.loads(
            subprocess.check_output(
                ["pactl", "--format=json", "list", "sinks"], timeout=2
            )
        )
        monitors = {sink["index"]: sink["monitor_source"] for sink in sinks}
        result = asyncio.run(sample(streams, monitors))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        print(f"audio_activity: {error}", file=sys.stderr)
        result = {str(stream["index"]): "unknown" for stream in streams}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
