#!/usr/bin/env bash
# Rewrite a palette atomically, then restore it even when interrupted.
# Usage: dusky-burst.sh [palette.css] [count] [interval-seconds]
set -euo pipefail
exec python3 - "$@" <<'PY'
import argparse
import math
from pathlib import Path
import re
import signal
import tempfile
import time

parser = argparse.ArgumentParser(description="Stress a Dusky palette and restore the original.")
parser.add_argument("palette", nargs="?", type=Path, default=Path.home() / ".config/matugen/generated/dusky_sites.css")
parser.add_argument("count", nargs="?", type=int, default=20)
parser.add_argument("interval", nargs="?", type=float, default=0.1)
args = parser.parse_args()
if args.count <= 0 or not math.isfinite(args.interval) or args.interval < 0:
    parser.error("count must be positive and interval must be finite and nonnegative")
path = args.palette.expanduser().resolve(strict=True)
original = path.read_bytes()
mode = path.stat().st_mode & 0o777
text = original.decode("utf-8")
pattern = re.compile(r"(--(?:dusky-palette-)?(primary|background|surface)\s*:\s*)#[0-9a-fA-F]{6}\b")
if not pattern.search(text):
    parser.error("palette has no supported hex color declarations")


def publish(data: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def interrupted(signum, _frame):
    raise SystemExit(128 + signum)


for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(sig, interrupted)
start = time.monotonic()
try:
    for i in range(1, args.count + 1):
        component = f"{i * 12 % 256:02x}"
        colors = {"primary": f"#{component}b4aa", "background": f"#1a11{component}", "surface": f"#{component}1110"}
        publish(pattern.sub(lambda match: match[1] + colors[match[2]], text).encode("utf-8"))
        time.sleep(args.interval)
finally:
    # A second termination signal must not interrupt restoration.
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    publish(original)
print(f"wrote {args.count} revisions in {time.monotonic() - start:.2f} s; restored original")
PY
