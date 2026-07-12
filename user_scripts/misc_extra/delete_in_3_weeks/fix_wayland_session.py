#!/usr/bin/env python3
"""
fix_wayland_session.py — wayland-session deployer

Modernized for Python 3.14 / Arch Linux (Kernel 7+).
Repairs /usr/local/bin/wayland-session by replacing stale uwsm invocations
with the native Hyprland 0.53+ launcher (start-hyprland).
"""

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────
WAYLAND_SESSION: Path = Path("/usr/local/bin/wayland-session")

# Defined with explicit '\n' to ensure deterministic SHA-256 hashes 
# regardless of environment line-ending configurations.
EXPECTED_CONTENT: str = "#!/usr/bin/env bash\nexec start-hyprland\n"
EXPECTED_HASH: str = hashlib.sha256(EXPECTED_CONTENT.encode("utf-8")).hexdigest()

# ── UI / TTY Colors ──────────────────────────────────────────────────────────
# Dynamically evaluate ANSI support for modern terminals
_USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ
_C_RESET = "\033[0m" if _USE_COLOR else ""
_C_BOLD = "\033[1m" if _USE_COLOR else ""
_C_GREEN = "\033[32m" if _USE_COLOR else ""
_C_YELLOW = "\033[33m" if _USE_COLOR else ""
_C_RED = "\033[31m" if _USE_COLOR else ""
_C_CYAN = "\033[36m" if _USE_COLOR else ""


def info(msg: str) -> None:
    print(f"{_C_GREEN}✔{_C_RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{_C_YELLOW}⚠{_C_RESET} {msg}", file=sys.stderr)


def fail(msg: str) -> None:
    print(f"{_C_RED}✖{_C_RESET} {msg}", file=sys.stderr)
    sys.exit(1)


# ── Root Escalation ──────────────────────────────────────────────────────────
def ensure_root() -> None:
    if os.geteuid() == 0:
        return
    warn("Root required — re-launching via sudo")
    try:
        # Modern argument unpacking
        os.execvp("sudo", ["sudo", "-p", "[sudo] password for %u: ", sys.executable, *sys.argv])
    except FileNotFoundError:
        fail("sudo not found. Run as root.")


# ── Core ─────────────────────────────────────────────────────────────────────
def write_atomic(target: Path, content: str, mode: int = 0o755) -> None:
    """
    Zero-trust atomic write leveraging Python 3.12+ NamedTemporaryFile, 
    descriptor-level fchmod (TOCTOU prevention), and strict O_CLOEXEC fsync.
    """
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None

    try:
        # delete=False explicitly hands over control of the file's lifecycle to us.
        with tempfile.NamedTemporaryFile(
            dir=parent,
            prefix=f".{target.name}.tmp.",
            mode="w",
            encoding="utf-8",
            newline="\n",
            delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
            tmp.flush()
            
            # Use fchmod on the file descriptor directly. This closes the TOCTOU 
            # vulnerability gap present when running chmod on file paths.
            os.fchmod(tmp.fileno(), mode)
            os.fsync(tmp.fileno())

        # POSIX atomic rename. Guarantees no partial file states exist on disk.
        os.replace(tmp_path, target)

    except OSError as e:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        fail(f"Atomic write failed: {e}")

    # Parent directory sync guarantees the directory entry is durable on power loss.
    try:
        # O_CLOEXEC prevents fd leakage on modern kernels.
        dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass  # Best-effort durability. Failure here does not invalidate the file write.


def main() -> None:
    ensure_root()
    print(f"{_C_BOLD}{_C_CYAN}wayland-session repair — Hyprland native launcher{_C_RESET}\n")

    hyprland_path = shutil.which("start-hyprland")
    if not hyprland_path:
        fail("start-hyprland not found in PATH. Is hyprland 0.53+ installed?")

    info(f"start-hyprland found: {hyprland_path}")

    current_content = ""
    if WAYLAND_SESSION.exists():
        try:
            current_content = WAYLAND_SESSION.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            fail(f"Could not read {WAYLAND_SESSION}: {e}")
    else:
        warn(f"{WAYLAND_SESSION} does not exist — will create it")

    if hashlib.sha256(current_content.encode("utf-8")).hexdigest() == EXPECTED_HASH:
        info(f"{WAYLAND_SESSION} already correct — nothing to do")
        sys.exit(0)

    if "uwsm" in current_content:
        warn(f"Detected stale uwsm invocation in {WAYLAND_SESSION}")
    elif current_content.strip():
        warn(f"{WAYLAND_SESSION} has unexpected content:")
        print(current_content, file=sys.stderr)

    info(f"Writing {WAYLAND_SESSION} → exec start-hyprland ({oct(0o755)})")
    write_atomic(WAYLAND_SESSION, EXPECTED_CONTENT, mode=0o755)

    # Post-write byte verification using Python 3.11+ hashlib.file_digest
    # This avoids reading the file back into memory, hashing the bytes straight from the disk.
    try:
        with open(WAYLAND_SESSION, "rb") as f:
            verify_hash = hashlib.file_digest(f, "sha256").hexdigest()
    except OSError as e:
        fail(f"Post-write verification failed to read file: {e}")

    if verify_hash == EXPECTED_HASH:
        info(f"Verified: {WAYLAND_SESSION} is strictly correct")
    else:
        fail("Post-write verification failed — byte mismatch detected")

    print(f"\n{_C_BOLD}{_C_GREEN}Done{_C_RESET} — restart greetd to test: {_C_CYAN}sudo systemctl restart greetd{_C_RESET}")


if __name__ == "__main__":
    main()
