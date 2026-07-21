#!/usr/bin/env -S python3 -I
"""SSHFS remote manager for current Arch Linux."""

import os
import sys

# This program should run as the normal user.
# It only needs escalation for pacman, not for mounting.
if os.geteuid() == 0 and any(
    var in os.environ
    for var in ("SUDO_USER", "SUDO_UID", "PKEXEC_UID", "DOAS_USER", "RUN0_UID")
):
    print(
        "[-] Run this script as your normal user, not via sudo/doas/run0/pkexec.\n"
        "    It will ask for administrative rights only when installing packages.",
        file=sys.stderr,
    )
    sys.exit(1)

import contextlib
import json
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Final, NoReturn

try:
    HOME: Final[Path] = Path.home()
except (RuntimeError, KeyError) as exc:
    print(f"[-] Cannot determine home directory: {exc}", file=sys.stderr)
    sys.exit(1)

# --- Fixed paths requested by the user ---
STATE_FILE: Final[Path] = HOME / ".config/dusky/settings/sshfiles/sshfs"
MOUNT_POINT: Final[Path] = HOME / "Documents/sshfs"

MAX_HISTORY: Final[int] = 10

# sshfs/FUSE/SSH options for a more stable interactive mount.
SSHFS_OPTIONS: Final[tuple[str, ...]] = (
    "reconnect",
    "ServerAliveInterval=15",
    "ServerAliveCountMax=3",
    "ConnectTimeout=10",
)


def fail(message: str, code: int = 1) -> NoReturn:
    print(f"[-] {message}", file=sys.stderr)
    sys.exit(code)


def root_hint(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return ""
    if st.st_uid == 0 and os.geteuid() != 0:
        quoted = shlex.quote(str(path))
        return f" If owned by root, fix with: sudo chown -R {os.getuid()}:{os.getgid()} {quoted}"
    return ""


def find_executable(name: str) -> str | None:
    path = shutil.which(name)
    if path:
        return path

    # Arch normally uses /usr/bin, but this makes the script more resilient
    # to stripped-down PATH values.
    for directory in ("/usr/local/bin", "/usr/bin", "/bin"):
        candidate = Path(directory, name)
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def read_input(prompt: str) -> str | None:
    try:
        return input(prompt).strip()
    except EOFError:
        return None


def confirm(prompt: str, default: bool = False) -> bool:
    answer = read_input(prompt)
    if answer is None:
        # Ctrl+D should cancel destructive/interactive actions.
        return False
    if not answer:
        return default
    return answer.lower() in {"y", "yes"}


def normalize_target(raw: str) -> str | None:
    """
    Validate and normalize an sshfs target.

    Accepted forms:
      user@host:/path
      host:/path
      user@host:
      ssh://user@host/path
      ssh://user@host:port/path

    Regular spaces are allowed in scp-like remote paths.
    Other whitespace and control characters are rejected.
    Password-bearing ssh:// URLs are rejected so they are not saved in history.
    """
    target = raw.strip()
    if not target or target.startswith("-"):
        return None
    if not target.isprintable():
        return None
    if any(ch in target for ch in "\r\n\t\v\f"):
        return None

    lowered = target.lower()

    # ssh:// style target
    if lowered.startswith("ssh://"):
        # URLs should use percent-encoding, not literal whitespace.
        if any(ch.isspace() for ch in target):
            return None

        rest = target[len("ssh://"):]
        if not rest:
            return None

        authority = rest.split("/", 1)[0]
        if not authority:
            return None

        if "@" in authority:
            userinfo, hostport = authority.rsplit("@", 1)
            if not userinfo or ":" in userinfo or "@" in userinfo or "/" in userinfo:
                return None
        else:
            hostport = authority

        host = hostport.rsplit(":", 1)[0] if ":" in hostport else hostport
        if not host or host.startswith("-"):
            return None

        if host.startswith("["):
            if not host.endswith("]") or len(host) <= 2:
                return None
        elif ":" in host:
            return None

        if "/" in host:
            return None

        return target

    # scp-like syntax: [user@]host:[path]
    if ":" not in target:
        return None

    host_part, path_part = target.split(":", 1)
    if not host_part:
        return None

    if any(ch.isspace() for ch in host_part):
        return None

    # Allow ordinary spaces in remote paths, but reject NBSP and other whitespace.
    if any(ch.isspace() and ch != " " for ch in path_part):
        return None

    if "@" in host_part:
        user, host = host_part.rsplit("@", 1)
        if not user or not host:
            return None
        if user.startswith("-") or ":" in user or "@" in user or "/" in user:
            return None
    else:
        host = host_part

    if not host or host.startswith("-"):
        return None

    if host.startswith("["):
        if not host.endswith("]") or len(host) <= 2:
            return None
    elif ":" in host:
        return None

    if "/" in host:
        return None

    return target


def _unescape_proc_field(field: bytes) -> bytes:
    """
    Unescape octal sequences from /proc/mounts fields.
    Example: b"/mnt/my\\040dir" -> b"/mnt/my dir"
    """
    out = bytearray()
    i = 0
    while i < len(field):
        # Backslash is 0x5C.
        if field[i] == 0x5C and i + 4 <= len(field):
            digits = field[i + 1:i + 4]
            if all(0x30 <= b <= 0x37 for b in digits):
                value = int(digits.decode("ascii"), 8)
                if value <= 0xFF:
                    out.append(value)
                    i += 4
                    continue
        out.append(field[i])
        i += 1
    return bytes(out)


def _mount_entries() -> Iterator[tuple[str, str, str]]:
    """
    Yield (source, target, fstype) from /proc/mounts.

    This avoids stat()ing the mountpoint, which can hang on stale FUSE mounts.
    """
    try:
        with open("/proc/mounts", "rb") as handle:
            for line in handle:
                fields = line.rstrip(b"\n").split(b" ")
                if len(fields) < 3:
                    continue

                source = os.fsdecode(_unescape_proc_field(fields[0]))
                target = os.fsdecode(_unescape_proc_field(fields[1]))
                fstype = os.fsdecode(_unescape_proc_field(fields[2]))
                yield source, target, fstype
    except OSError:
        return


def get_mount_info() -> tuple[str, str] | None:
    """
    Return (source, fstype) if MOUNT_POINT is currently mounted.
    """
    wanted = os.path.normpath(str(MOUNT_POINT))
    for source, target, fstype in _mount_entries():
        if os.path.normpath(target) == wanted:
            return source, fstype
    return None


def state_destination() -> Path:
    """
    If STATE_FILE is a symlink, write through to its target so the symlink
    itself is preserved.
    """
    if STATE_FILE.is_symlink():
        try:
            return STATE_FILE.resolve(strict=False)
        except OSError:
            return STATE_FILE
    return STATE_FILE


def write_state(history: list[str]) -> None:
    """
    Atomically write state JSON.
    """
    destination = state_destination()
    text = json.dumps({"history": history}, indent=4, ensure_ascii=False) + "\n"

    fd, tmp_name = tempfile.mkstemp(
        prefix=destination.name + ".",
        suffix=".tmp",
        dir=destination.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, destination)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise

    # Best-effort directory fsync for durability.
    with contextlib.suppress(OSError):
        dir_fd = os.open(destination.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def load_state() -> list[str]:
    """
    Load history from STATE_FILE.
    Invalid entries are discarded.
    """
    try:
        raw = STATE_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        print(f"[!] Cannot read state file: {exc}", file=sys.stderr)
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[!] State file is corrupt; starting with empty history ({exc}).", file=sys.stderr)
        return []

    if isinstance(data, dict):
        history = data.get("history", [])
    elif isinstance(data, list):
        history = data
    else:
        history = []

    if not isinstance(history, list):
        return []

    cleaned: list[str] = []
    for item in history:
        if not isinstance(item, str):
            continue
        normalized = normalize_target(item)
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)

    return cleaned[:MAX_HISTORY]


def save_state(history: list[str], new_entry: str | None = None) -> list[str]:
    """
    Deduplicate, trim to MAX_HISTORY, and atomically save.
    """
    cleaned: list[str] = []
    candidates: list[str] = []

    if new_entry is not None:
        candidates.append(new_entry)
    candidates.extend(history)

    for item in candidates:
        normalized = normalize_target(item) if isinstance(item, str) else None
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)

    cleaned = cleaned[:MAX_HISTORY]
    write_state(cleaned)
    return cleaned


def update_history(history: list[str], target: str) -> list[str]:
    try:
        return save_state(history, target)
    except OSError as exc:
        print(f"[!] Could not save state: {exc}", file=sys.stderr)
        hint = root_hint(state_destination()) or root_hint(state_destination().parent)
        if hint:
            print(hint, file=sys.stderr)
        return history


def ensure_state_file() -> None:
    if not state_destination().exists():
        try:
            write_state([])
        except OSError as exc:
            fail(f"Cannot create state file {STATE_FILE}: {exc}{root_hint(STATE_FILE.parent)}")


def ensure_directories() -> None:
    if not STATE_FILE.is_absolute() or not MOUNT_POINT.is_absolute():
        fail("State and mount paths must be absolute. Check HOME.")

    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        fail(f"Cannot create state directory {STATE_FILE.parent}: {exc}{root_hint(STATE_FILE.parent)}")

    destination = state_destination()

    if destination.is_dir():
        fail(f"{destination} is a directory; expected a state file.")

    if not os.access(destination.parent, os.W_OK | os.X_OK):
        fail(f"State directory {destination.parent} is not writable.{root_hint(destination.parent)}")

    if destination.exists() and not os.access(destination, os.R_OK | os.W_OK):
        fail(f"State file {destination} is not readable/writable.{root_hint(destination)}")

    # Avoid traversing the mountpoint if it is already mounted.
    if get_mount_info() is None:
        # If the mountpoint itself is a symlink, do not stat through it here;
        # it could point at a stale FUSE mount.
        if MOUNT_POINT.is_symlink():
            return

        try:
            MOUNT_POINT.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            fail(f"Cannot create mountpoint {MOUNT_POINT}: {exc}{root_hint(MOUNT_POINT.parent)}")

        if not MOUNT_POINT.is_dir():
            fail(f"{MOUNT_POINT} exists and is not a directory.")

        if not os.access(MOUNT_POINT, os.W_OK | os.X_OK):
            fail(f"Mountpoint {MOUNT_POINT} is not writable/executable.{root_hint(MOUNT_POINT)}")


def install_package(package: str) -> bool:
    pacman = find_executable("pacman")
    if not pacman:
        print("[-] pacman not found.", file=sys.stderr)
        return False

    base: list[str] | None = None

    if os.geteuid() == 0:
        base = []
    else:
        for escalator in ("sudo", "doas", "run0"):
            path = find_executable(escalator)
            if path:
                base = [path]
                break

    if base is None:
        print(
            f"[-] No privilege command found (sudo/doas/run0). Install '{package}' manually.",
            file=sys.stderr,
        )
        return False

    cmd = base + [pacman, "-S", "--noconfirm", "--noprogressbar", package]

    print(f"[*] Installing '{package}' via pacman...")
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"[-] pacman failed with exit code {exc.returncode}.", file=sys.stderr)

        if Path("/var/lib/pacman/db.lck").exists():
            print("    Hint: Pacman appears to be locked by another process.", file=sys.stderr)

        print("    Hint: If package databases/keyrings are outdated, run a full system upgrade first.", file=sys.stderr)
        print(f"    Hint: Check package availability with: pacman -Ss {package}", file=sys.stderr)
        return False
    except OSError as exc:
        print(f"[-] Failed to run pacman: {exc}", file=sys.stderr)
        return False


def ensure_unmount_dependencies() -> bool:
    if find_executable("fusermount3"):
        return True

    print("[-] fusermount3 not found. Installing 'fuse3'...")
    if not install_package("fuse3"):
        return False

    if not find_executable("fusermount3"):
        print("[-] fusermount3 is still missing after installing fuse3.", file=sys.stderr)
        return False

    return True


def ensure_mount_dependencies() -> bool:
    requirements = (
        ("sshfs", "sshfs"),
        ("fusermount3", "fuse3"),
        ("ssh", "openssh"),
    )

    for binary, package in requirements:
        if find_executable(binary):
            continue

        print(f"[-] '{binary}' not found. Installing '{package}'...")
        if not install_package(package):
            return False

        if not find_executable(binary):
            print(f"[-] '{binary}' is still missing after installing '{package}'.", file=sys.stderr)
            return False

    return True


def check_fuse_device() -> bool:
    fuse = Path("/dev/fuse")

    if not fuse.exists():
        print(
            "[-] /dev/fuse is missing. Install fuse3 and ensure the fuse kernel module is loaded.",
            file=sys.stderr,
        )
        return False

    if os.geteuid() != 0 and not os.access(fuse, os.R_OK | os.W_OK):
        print("[-] /dev/fuse is not accessible by your user.", file=sys.stderr)
        print("    Hint: Install fuse3, reboot or re-login, and ensure udev gives /dev/fuse mode 0666.", file=sys.stderr)
        return False

    return True


def wait_until_unmounted(timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get_mount_info() is None:
            return True
        time.sleep(0.1)
    return get_mount_info() is None


def mount_appeared() -> bool:
    if get_mount_info() is not None:
        return True

    # Fallback for symlinked mountpoints where /proc/mounts path may differ.
    if MOUNT_POINT.is_symlink():
        with contextlib.suppress(OSError):
            return os.path.ismount(MOUNT_POINT)

    return False


def wait_until_mounted(timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if mount_appeared():
            return True
        time.sleep(0.1)
    return mount_appeared()


def unmount(*, lazy: bool = False, interactive: bool = False) -> bool:
    info = get_mount_info()

    # Not detected in /proc/mounts.
    # Still attempt a best-effort fusermount3 unmount in case detection missed
    # because of symlink/path canonicalization edge cases.
    if info is None:
        fusermount = find_executable("fusermount3")
        if fusermount:
            try:
                proc = subprocess.run(
                    [fusermount, "-u", str(MOUNT_POINT)],
                    check=False,
                    text=True,
                    capture_output=True,
                )
            except OSError:
                proc = None

            if proc is not None and proc.returncode == 0 and wait_until_unmounted():
                print("[+] Unmounted successfully.")
                return True

        print("[*] Directory is not currently mounted.")
        return True

    source, fstype = info

    if not (fstype == "fuse" or fstype.startswith("fuse.")):
        print(
            f"[-] {MOUNT_POINT} is mounted as '{fstype}', not FUSE. Refusing to unmount.",
            file=sys.stderr,
        )
        return False

    if fstype != "fuse.sshfs":
        print(f"[!] Warning: expected fuse.sshfs, but found '{fstype}'.")

    fusermount = find_executable("fusermount3")
    if not fusermount:
        if not ensure_unmount_dependencies():
            return False

        fusermount = find_executable("fusermount3")
        if not fusermount:
            print("[-] fusermount3 is still missing.", file=sys.stderr)
            return False

    cmd = [fusermount, "-u"]
    if lazy:
        cmd.append("-z")
    cmd.append(str(MOUNT_POINT))

    action = "Lazily unmounting" if lazy else "Unmounting"
    print(f"[*] {action} {MOUNT_POINT} ({source})...")

    try:
        proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    except OSError as exc:
        print(f"[-] Failed to run fusermount3: {exc}", file=sys.stderr)
        return False

    if proc.returncode == 0:
        if wait_until_unmounted():
            print("[+] Unmounted successfully.")
            return True

        print("[-] Unmount command succeeded but the mount is still present.", file=sys.stderr)
        return False

    output = (proc.stderr or proc.stdout or "").strip()
    if output:
        print(output, file=sys.stderr)

    if not lazy and interactive:
        if confirm("[?] Normal unmount failed. Try lazy unmount (fusermount3 -u -z)? [y/N]: ", default=False):
            return unmount(lazy=True, interactive=False)

    return False


def mount(raw_target: str) -> bool:
    target = normalize_target(raw_target)
    if target is None:
        print(
            "[-] Invalid target. Use user@host:/path, host:/path, or ssh://user@host/path.",
            file=sys.stderr,
        )
        return False

    info = get_mount_info()
    if info is not None:
        print(f"[*] {MOUNT_POINT} is already mounted ({info[0]}).")
        if not confirm("[?] Unmount it and continue? [Y/n]: ", default=True):
            print("[*] Mount cancelled.")
            return False

        if not unmount(interactive=True):
            print("[-] Cannot mount while the existing mount is active.", file=sys.stderr)
            return False

    extra_options: list[str] = []

    try:
        if not MOUNT_POINT.is_symlink():
            MOUNT_POINT.mkdir(parents=True, exist_ok=True, mode=0o700)

            if not MOUNT_POINT.is_dir():
                print(f"[-] {MOUNT_POINT} exists and is not a directory.", file=sys.stderr)
                return False

            if any(MOUNT_POINT.iterdir()):
                if not confirm(
                    f"[!] {MOUNT_POINT} is not empty. Mounting will hide existing files. Continue? [y/N]: ",
                    default=False,
                ):
                    print("[*] Mount cancelled.")
                    return False

                extra_options.append("nonempty")
    except OSError as exc:
        print(f"[-] Cannot inspect mountpoint: {exc}", file=sys.stderr)
        return False

    if not ensure_mount_dependencies():
        return False

    if not check_fuse_device():
        return False

    sshfs = find_executable("sshfs")
    if not sshfs:
        print("[-] sshfs is missing.", file=sys.stderr)
        return False

    # If the mountpoint is a symlink, do a quiet best-effort cleanup first.
    # This helps with stale or canonicalization-mismatched FUSE mounts.
    if MOUNT_POINT.is_symlink():
        cleanup = find_executable("fusermount3")
        if cleanup:
            with contextlib.suppress(OSError):
                subprocess.run(
                    [cleanup, "-u", str(MOUNT_POINT)],
                    check=False,
                    text=True,
                    capture_output=True,
                )

    options = list(SSHFS_OPTIONS) + extra_options

    cmd = [sshfs]
    for option in options:
        cmd.extend(("-o", option))
    cmd.extend((target, str(MOUNT_POINT)))

    print(f"[*] Mounting {target} at {MOUNT_POINT}...")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(
            f"[-] Mounting failed (exit code {exc.returncode}). "
            "Verify target, remote path, SSH credentials, and server SFTP access.",
            file=sys.stderr,
        )
        return False
    except OSError as exc:
        print(f"[-] Failed to run sshfs: {exc}", file=sys.stderr)
        return False

    if wait_until_mounted():
        print(f"[+] Successfully mounted. Access files at {MOUNT_POINT}")
        print("    Hint: Automatic reconnect works best with SSH key authentication.")
        return True

    print("[-] sshfs exited successfully but the mount did not appear.", file=sys.stderr)
    return False


def main() -> int:
    ensure_directories()
    history = load_state()
    ensure_state_file()

    print("=== SSHFS Remote Manager ===")

    while True:
        info = get_mount_info()
        if info:
            print(f"\nCurrent mount: {info[0]} -> {MOUNT_POINT} ({info[1]})")
        else:
            print(f"\nNot mounted: {MOUNT_POINT}")

        menu: list[tuple[str, str]] = [
            ("1", "Connect to a new server (user@host:/path or ssh://user@host[:port]/path)")
        ]
        if history:
            menu.append(("2", "Connect to a recently used server"))
        menu.append(("3", "Unmount current connection"))
        menu.append(("4", "Exit"))

        print("\nOptions:")
        for key, label in menu:
            print(f"{key}. {label}")

        choice = read_input(f"\nSelect an option ({'/'.join(key for key, _ in menu)}): ")
        if choice is None:
            print("\n[*] Exiting...")
            break

        choice = choice.lower()
        if choice.isdecimal():
            choice = str(int(choice))

        match choice:
            case "1":
                target = read_input(
                    "Enter SSH target (e.g., user@192.168.1.50:/home/user or ssh://user@host:2222/path): "
                )
                if target is None:
                    print("\n[*] Exiting...")
                    break

                if not target:
                    print("[-] No target entered.")
                    continue

                if mount(target):
                    history = update_history(history, target)

            case "2" if history:
                print("\nRecent connections:")
                for i, entry in enumerate(history, 1):
                    print(f"  {i}. {entry}")

                raw = read_input(f"Select connection (1-{len(history)}): ")
                if raw is None:
                    print("\n[*] Exiting...")
                    break

                try:
                    index = int(raw)
                except ValueError:
                    print("[-] Invalid selection.")
                    continue

                if not 1 <= index <= len(history):
                    print("[-] Invalid selection.")
                    continue

                target = history[index - 1]
                if mount(target):
                    history = update_history(history, target)

            case "3":
                unmount(interactive=True)

            case "4" | "q" | "quit" | "exit":
                print("[*] Exiting...")
                break

            case _:
                print("[-] Invalid option. Please enter a valid number.")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[*] Exiting...")
        sys.exit(0)
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            sys.stdout.close()
        sys.exit(0)
