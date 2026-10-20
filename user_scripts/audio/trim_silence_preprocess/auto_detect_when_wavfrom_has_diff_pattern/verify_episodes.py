#!/usr/bin/env python3
"""Interactive MPV batch verifier for auto-detected episode starts."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

DEFAULT_DETECTIONS_PATH = (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
                           / "dusky/settings/audio_detection/detections.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('detections', type=Path, nargs='?',
                        default=DEFAULT_DETECTIONS_PATH,
                        help='Detection report JSON (default: %(default)s)')
    args = parser.parse_args()
    json_path = args.detections.expanduser().resolve()
    if not json_path.exists():
        print(f"Error: Detection file not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    episodes = json.loads(json_path.read_text(encoding="utf-8"))
    print("=" * 85)
    print(f"Loaded {len(episodes)} auto-detected episodes for sequential verification.")
    print("How it works: Listen to each episode at the detected timestamp.")
    print("Press 'q' (or close the MPV window) to immediately advance to the next episode.")
    print("Press Ctrl+C in terminal to abort at any time.")
    print("=" * 85 + "\n")

    for idx, ep in enumerate(episodes, 1):
        filename = ep["filename"]
        filepath = ep["filepath"]
        start_ts = ep["show_start_timestamp"]
        total_dur = ep["total_duration_sec"]
        hh = int(total_dur // 3600)
        mm = int((total_dur % 3600) // 60)
        ss = int(total_dur % 60)
        total_ts = f"{hh:02d}:{mm:02d}:{ss:02d}"
        sig = ep["transition_signature"]

        print(f"\n[{idx}/{len(episodes)}] {filename}")
        print(f"  -> Total: {total_ts} | Detected Start: {start_ts} | Signature: {sig}")
        print("  -> Launching MPV (press 'q' in window to go to next)...")

        cmd = [
            "mpv",
            "--force-window=immediate",
            "--no-resume-playback",
            "--cache-on-disk=no",
            "--script-opts-append=dusky_silence-enabled=no",
            "--geometry=850x400",
            f"--osd-msg1=[Episode {idx}/{len(episodes)}] Start: {start_ts} | Press 'q' for next",
            "--osd-level=3",
            f"--start={start_ts}",
            filepath,
        ]
        try:
            subprocess.run(cmd, check=False)
        except KeyboardInterrupt:
            print("\nVerification aborted by user.", file=sys.stderr)
            sys.exit(130)
        except OSError as error:
            print(f"MPV launch failed: {error}", file=sys.stderr)
            sys.exit(1)

    print(f"\nAll {len(episodes)} episodes reviewed!")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Verification failed: {error}", file=sys.stderr)
        sys.exit(1)
