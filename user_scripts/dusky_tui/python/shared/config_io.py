"""Shared read snapshots and atomic UTF-8 commits for configuration engines."""
import os
lazy import stat
lazy import tempfile
lazy import json
lazy import subprocess
lazy import sys
lazy from pathlib import Path

type FileStamp = tuple[int, int, int, int, int]


def stamp(info: os.stat_result) -> FileStamp:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def current_stamp(path: Path) -> FileStamp | None:
    try:
        return stamp(path.stat())
    except FileNotFoundError:
        return None


def split_lines(text: str) -> list[str]:
    """Split Linux configuration lines without treating Unicode as a newline."""
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def read_text(path: Path) -> tuple[str, FileStamp | None]:
    try:
        with open(path, encoding="utf-8", newline="") as stream:
            before = stamp(os.fstat(stream.fileno()))
            text = stream.read()
            if stamp(os.fstat(stream.fileno())) != before:
                raise OSError(f"File {path.name} changed while reading. Reload required.")
        return text, before
    except FileNotFoundError:
        return "", None


def atomic_write(path: Path, text: str, expected: FileStamp | None) -> FileStamp:
    """Commit one file; detect changes since reading and clean up failed writes.

    Snapshot checks are optimistic: an unrelated writer can still race the final
    check and rename. Atomic replacement does not make a multi-file transaction.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8",
                                         newline="", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            info = path.stat() if expected is not None else None
            if info is not None:
                temp_info = os.fstat(stream.fileno())
                if (temp_info.st_uid, temp_info.st_gid) != (info.st_uid, info.st_gid):
                    os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode) if info is not None else 0o644)
            os.fsync(stream.fileno())
        if current_stamp(path) != expected:
            raise OSError(f"File {path.name} was modified externally. Reload required.")
        os.replace(temporary, path)
        # Rename can change ctime; observe the committed inode after replacement.
        committed = stamp(path.stat())
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            raise OSError(f'Configuration saved, but directory sync failed: {exc}') from exc
        finally:
            os.close(directory_fd)
        return committed
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def privileged_atomic_write(path: Path, text: str, expected: FileStamp | None) -> FileStamp:
    """Use the frontend's sudo credential cache without truncating the target."""
    code = '''import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from python.shared.config_io import atomic_write
payload = json.load(sys.stdin)
try:
    expected = tuple(payload["expected"]) if payload["expected"] is not None else None
    result = {"stamp": atomic_write(Path(payload["path"]), payload["text"], expected)}
except (OSError, UnicodeError) as exc:
    result = {"error": str(exc)}
print(json.dumps(result))
'''
    result = subprocess.run(
        ['sudo', '-n', sys.executable, '-c', code, str(Path(__file__).resolve().parents[2])],
        input=json.dumps({'path': str(path), 'text': text, 'expected': expected}),
        text=True, encoding='utf-8', capture_output=True, timeout=10,
    )
    if result.returncode:
        raise PermissionError(result.stderr.strip() or 'Sudo authorization required.')
    payload = json.loads(result.stdout)
    if 'error' in payload:
        raise OSError(payload['error'])
    return tuple(payload['stamp'])


def boolean(value: object) -> bool:
    return value.strip().lower() in {"true", "1", "yes", "on", "t", "y"} if isinstance(value, str) else bool(value)


def line_value(value: object, item_type: str) -> str:
    text = "" if value is None else str(value)
    if text.startswith("__VAR__"):
        text = text[7:]
    if text in {"nil", "__DELETE__"}:
        return "__DELETE__"
    if item_type == "bool":
        text = "true" if boolean(value) else "false"
    if "\n" in text or "\r" in text or "\0" in text:
        raise ValueError("Line-based configuration values cannot contain newlines or NUL.")
    return text
