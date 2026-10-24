#!/usr/bin/env python3
"""
Looking Glass Client Configuration
Target: Arch rolling (Aug 2026) / Python 3.15+ / Wayland / Hyprland
Policy: Atomic, idempotent, operator-owned (not root), native <shmem> path.
"""

import os
import stat
import sys
import tempfile
from pathlib import Path
import json

MIN_PY: tuple[int, int, int] = (3, 15, 0)
if sys.version_info[:3] < MIN_PY:
    sys.stderr.write(f"\n[FATAL] Python {MIN_PY[0]}.{MIN_PY[1]}.{MIN_PY[2]}+ required; running {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}.\n\n")
    raise SystemExit(1)

STATE_FILE = Path("/var/lib/arsonix/state.json")
SHM_DEFAULT = "/dev/shm/looking-glass"

def get_caller_identity() -> tuple[Path, int, int]:
    """Resolves the real user when running under sudo or standard shell."""
    sudo_user = os.environ.get("SUDO_USER") or os.environ.get("DOAS_USER")
    if sudo_user and sudo_user != "root":
        import pwd
        try:
            user_info = pwd.getpwnam(sudo_user)
            return Path(user_info.pw_dir), user_info.pw_uid, user_info.pw_gid
        except KeyError:
            pass
    return Path.home(), os.getuid(), os.getgid()

def atomic_write(path: Path, content: str, uid: int, gid: int) -> None:
    """Atomic mkstemp+os.replace+fsync, preserving 0644 and operator ownership."""
    missing: list[Path] = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        if os.geteuid() == 0:
            os.chown(directory, uid, gid)
    # Ensure parent owned correctly if running as root
    if os.geteuid() == 0:
        try:
            os.chown(path.parent, uid, gid)
        except OSError:
            pass
    keep_mode = 0o644
    if path.exists():
        try:
            keep_mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            pass
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, keep_mode)
        try:
            os.chown(tmp, uid, gid)
        except OSError as e:
            print(f"Warning: Failed to set tmp ownership: {e}")
        os.replace(tmp, path)
        try:
            dir_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
        # Final chown ensures file owned by operator even if parent was root-owned
        if os.geteuid() == 0:
            try:
                os.chown(path, uid, gid)
            except OSError as e:
                print(f"Warning: Failed to set file ownership: {e}")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

def resolve_shm_path() -> str:
    """Prefer state-recorded shm_path (Phase 5), fallback to canonical."""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cand = data.get("shm_path")
            if isinstance(cand, str) and cand.startswith("/dev/shm/"):
                return cand
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return SHM_DEFAULT

def merge_config(content: str, shm_file: str) -> str:
    """Converge managed keys while retaining comments and other user options.

    [app] shmFile is a deprecated alias for [lgmp] shmDevice. Never put the
    canonical key in [app]: the current client ignores app:shmDevice.
    """
    required = {
        "lgmp": {"shmdevice": f"shmDevice={shm_file}"},
        "spice": {"enable": "enable=yes", "clipboard": "clipboard=yes"},
    }
    emitted: set[str] = set()
    result: list[str] = []
    section = ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].lower()
            result.append(line)
            if section in required and section not in emitted:
                result.extend(required[section].values())
                emitted.add(section)
            continue
        if stripped and not stripped.startswith((";", "#")) and "=" in stripped:
            key = stripped.split("=", 1)[0].strip().lower()
            if section in ("app", "lgmp") and key in ("shmfile", "shmdevice"):
                continue
            if key in required.get(section, {}):
                continue
        result.append(line)
    for section, keys in required.items():
        if section not in emitted:
            result.extend(["", f"[{section}]", *keys.values()])
    return "\n".join(result) + "\n"


def main() -> None:
    home_dir, uid, gid = get_caller_identity()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home_dir / ".config")
    if not config_home.is_absolute():
        config_home = home_dir / ".config"
    config_dir = config_home / "looking-glass"
    config_file = config_dir / "client.ini"
    shm_file = resolve_shm_path()

    default_config = f"""; Looking Glass Client Configuration
; Hyprland / Wayland; install matching client and guest builds.

[lgmp]
shmDevice={shm_file}
allowDMA=yes

[egl]
vsync=no

[wayland]
fractionScale=yes
warpSupport=yes

[win]
autoResize=yes
keepAspect=yes
dontUpscale=yes
noScreensaver=yes
borderless=yes

[input]
escapeKey=KEY_F6
rawMouse=yes
hideCursor=yes

[spice]
enable=yes
clipboard=yes
audio=yes
"""
    if config_file.exists():
        content = config_file.read_text(encoding="utf-8")
        new_content = merge_config(content, shm_file)
        if new_content == content:
            print("Looking Glass shared memory and SPICE settings already configured.")
            return
    else:
        new_content = default_config
    atomic_write(config_file, new_content, uid, gid)
    print(f"Looking Glass configuration saved to {config_file}.")


if __name__ == "__main__":
    main()
