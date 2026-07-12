#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_wayland_session.py — wayland-session deployer (Python 3.14.6)

Repairs /usr/local/bin/wayland-session by replacing stale uwsm invocations
with the native Hyprland 0.53+ launcher (start-hyprland).

Features:
  - Atomic writes with fsync + parent dir sync (power-loss safe)
  - Idempotent: no-ops if already correct
  - Pre-flight validation: checks start-hyprland exists, file is writable
  - SHA-256 change detection to skip unnecessary writes
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────
WAYLAND_SESSION: Path = Path("/usr/local/bin/wayland-session")

EXPECTED_CONTENT: str = """\
#!/usr/bin/env bash
exec start-hyprland
"""

# ── TTY Colors (respect NO_COLOR / non-TTY) ─────────────────────────────────
if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
    _R = "\033[0m"
    _B = "\033[1m"
    _G = "\033[32m"
    _Y = "\033[33m"
    _R_ = "\033[31m"
    _C = "\033[36m"
else:
    _R = _B = _G = _Y = _R_ = _C = ""


def info(msg: str) -> None:
    print(f"{_G}✔{_R} {msg}")


def warn(msg: str) -> None:
    print(f"{_Y}⚠{_R} {msg}", file=sys.stderr)


def fail(msg: str) -> None:
    print(f"{_R_}✖{_R} {msg}", file=sys.stderr)
    sys.exit(1)


# ── Root escalation ──────────────────────────────────────────────────────────
def ensure_root() -> None:
    if os.geteuid() == 0:
        return
    warn("Root required — re-launching via sudo")
    try:
        os.execvp("sudo", ["sudo", "-p", "[sudo] password for %u: ", sys.executable] + sys.argv)
    except FileNotFoundError:
        fail("sudo not found. Run as root.")


# ── Core ─────────────────────────────────────────────────────────────────────
def sha256(data: str | bytes) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def write_atomic(target: Path, content: str, mode: int = 0o755) -> bool:
    """Write to target atomically: tmp → fsync → rename → fsync parent dir."""
    parent = target.parent
    fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=f".{target.name}.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        os.chmod(tmp, mode)
        os.replace(tmp, target)

        # fsync parent directory to guarantee linkage on power loss
        dir_fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

        return True
    except OSError as e:
        warn(f"write failed: {e}")
        return False
    finally:
        Path(tmp).unlink(missing_ok=True)


def main() -> None:
    ensure_root()
    print(f"{_B}{_C}wayland-session repair — Hyprland native launcher{_R}\n")

    # Pre-flight: start-hyprland must exist
    if not shutil.which("start-hyprland"):
        fail("start-hyprland not found in PATH. Is hyprland 0.53+ installed?")

    info(f"start-hyprland found: {shutil.which('start-hyprland')}")

    # Read current content
    if WAYLAND_SESSION.exists():
        current = WAYLAND_SESSION.read_text(encoding="utf-8", errors="replace")
    else:
        warn(f"{WAYLAND_SESSION} does not exist — will create it")
        current = ""

    # Idempotent check
    expected_hash = sha256(EXPECTED_CONTENT)
    current_hash = sha256(current)

    if current_hash == expected_hash:
        info(f"{WAYLAND_SESSION} already correct — nothing to do")
        sys.exit(0)

    # Diagnose what's wrong
    if "uwsm" in current:
        warn(f"Detected stale uwsm invocation in {WAYLAND_SESSION}")
    elif current.strip():
        warn(f"{WAYLAND_SESSION} has unexpected content:")
        print(current, file=sys.stderr)

    # Atomic write
    info(f"Writing {WAYLAND_SESSION} → exec start-hyprland ({oct(0o755)})")
    if not write_atomic(WAYLAND_SESSION, EXPECTED_CONTENT, mode=0o755):
        fail("Write failed — check permissions (try running with sudo)")

    # Verify
    verify = WAYLAND_SESSION.read_text(encoding="utf-8")
    if sha256(verify) == expected_hash:
        info(f"Verified: {WAYLAND_SESSION} is correct")
    else:
        fail("Post-write verification failed")

    print(f"\n{_B}{_G}Done{_R} — restart greetd to test: {_C}sudo systemctl restart greetd{_R}")


if __name__ == "__main__":
    main()
