#!/usr/bin/env python3
"""
Looking Glass Client Configuration
Target: Arch rolling (Aug 2026) / Python 3.14.7+ / Wayland / Hyprland
Policy: Atomic, idempotent, operator-owned (not root), native <shmem> path.
"""

import os
import stat
import sys
import tempfile
from pathlib import Path
import json

MIN_PY: tuple[int, int, int] = (3, 14, 7)
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
    path.parent.mkdir(parents=True, exist_ok=True)
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

def main() -> None:
    home_dir, uid, gid = get_caller_identity()
    config_dir = home_dir / ".config" / "looking-glass"
    config_file = config_dir / "client.ini"
    shm_file = resolve_shm_path()

    # Ensure config dir exists with correct ownership (atomic via mkdir + chown)
    config_dir.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        try:
            os.chown(config_dir, uid, gid)
        except OSError as e:
            print(f"Warning: Failed to set directory ownership: {e}")

    default_config = f"""; Looking Glass Client Configuration
; Tailored for Hyprland / Wayland / Looking Glass B7+

[lgmp]
shmDevice={shm_file}
allowDMA=yes

[egl]
vsync=no

[wayland]
fractionScale=no
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

    if not config_file.exists():
        print(f"Creating new Looking Glass client configuration at {config_file}...")
        atomic_write(config_file, default_config, uid, gid)
        print("Configuration file created successfully with SPICE clipboard enabled.")
        return

    print(f"Existing configuration found at {config_file}. Merging/checking configuration...")
    content = config_file.read_text(encoding="utf-8")

    # Parse and ensure required keys
    lines = content.splitlines()
    has_lgmp = False
    has_shm = False
    has_spice_section = False
    has_enable = False
    has_clipboard = False
    current_section: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1].lower()
            if current_section in ("lgmp", "app"):
                has_lgmp = True
            if current_section == "spice":
                has_spice_section = True
        elif current_section in ("lgmp", "app") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip().lower()
            if key in ("shmdevice", "shmfile"):
                val = stripped.split("=", 1)[1].strip()
                if val == shm_file:
                    has_shm = True
        elif current_section == "spice" and "=" in stripped:
            key = stripped.split("=", 1)[0].strip().lower()
            val = stripped.split("=", 1)[1].strip().lower() if "=" in stripped else ""
            if key == "enable" and val in ("yes", "true", "1"):
                has_enable = True
            elif key == "clipboard" and val in ("yes", "true", "1"):
                has_clipboard = True

    # If all correct, nothing to do
    if has_shm and has_spice_section and has_enable and has_clipboard:
        print("SPICE clipboard and shmDevice settings are already correctly configured in client.ini.")
        return

    # Rebuild with fixes: ensure shmDevice correct, ensure spice section correct
    new_lines: list[str] = []
    in_lgmp = False
    in_spice = False
    injected_shm = has_shm
    injected_enable = has_enable
    injected_clipboard = has_clipboard
    found_lgmp = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            # Close previous sections: inject missing keys before leaving section
            if in_lgmp and not injected_shm:
                new_lines.append(f"shmDevice={shm_file}")
                injected_shm = True
            if in_spice:
                if not injected_enable:
                    new_lines.append("enable=yes")
                    injected_enable = True
                if not injected_clipboard:
                    new_lines.append("clipboard=yes")
                    injected_clipboard = True
            in_lgmp = stripped[1:-1].lower() in ("lgmp", "app")
            in_spice = stripped[1:-1].lower() == "spice"
            if in_lgmp:
                found_lgmp = True
            new_lines.append(line)
            continue

        if in_lgmp and "=" in stripped:
            key = stripped.split("=", 1)[0].strip().lower()
            if key in ("shmdevice", "shmfile"):
                if stripped.split("=", 1)[1].strip() != shm_file or key == "shmfile":
                    new_lines.append(f"shmDevice={shm_file}")
                    injected_shm = True
                else:
                    new_lines.append(line)
                    injected_shm = True
                continue
        if in_spice and "=" in stripped:
            key = stripped.split("=", 1)[0].strip().lower()
            if key == "enable" and not has_enable:
                new_lines.append("enable=yes")
                injected_enable = True
                continue
            if key == "clipboard" and not has_clipboard:
                new_lines.append("clipboard=yes")
                injected_clipboard = True
                continue
        new_lines.append(line)

    # Handle files that ended inside a section without closing
    if in_lgmp and not injected_shm:
        new_lines.append(f"shmDevice={shm_file}")
    if in_spice:
        if not injected_enable:
            new_lines.append("enable=yes")
        if not injected_clipboard:
            new_lines.append("clipboard=yes")

    # Ensure [lgmp] exists at all
    if not found_lgmp:
        insert_at = 0
        for i, l in enumerate(new_lines):
            if l.strip().startswith("["):
                insert_at = i
                break
        new_lines.insert(insert_at, f"shmDevice={shm_file}")
        new_lines.insert(insert_at, "[lgmp]")

    # Ensure [spice] exists
    if not has_spice_section:
        print("Adding [spice] section to client.ini...")
        new_lines.append("")
        new_lines.append("[spice]")
        new_lines.append("enable=yes")
        new_lines.append("clipboard=yes")
        new_lines.append("audio=yes")
    elif not has_enable or not has_clipboard:
        print("Updating parameters in [spice] section...")

    # Also ensure shmDevice fix is reported
    if not has_shm:
        print(f"Updating shmDevice to {shm_file}...")

    new_content = "\n".join(new_lines) + "\n"
    # Normalize consecutive blank lines
    import re
    new_content = re.sub(r"\n{3,}", "\n\n", new_content)
    atomic_write(config_file, new_content, uid, gid)
    print("Configuration updated successfully.")

if __name__ == "__main__":
    main()
