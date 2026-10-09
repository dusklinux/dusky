#!/usr/bin/env python3
"""Waybar theme selection, persistent state, and serialized process restarts."""
import os
import re
from pathlib import Path
from contextlib import contextmanager
lazy from contextlib import ExitStack
lazy import fcntl
lazy import json
lazy import select
lazy import shutil
lazy import signal
lazy import subprocess
lazy import tempfile
lazy import threading
lazy import time
lazy from typing import Any

from python.frontend.core_types import BaseEngine

# Strings are tokens, so comment markers and braces inside them stay untouched.
_JSONC_TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|[{}\[\]:,]|[^\s{}\[\]:,]+', re.DOTALL)
_OPPOSITE = frozendict(top="bottom", bottom="top", left="right", right="left")
type Change = tuple[str, str, str, str]
type WriteResult = tuple[bool, str, str]


def discover_themes(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.glob("*/config.jsonc") if path.is_file())


def _position_token(content: str) -> re.Match[str]:
    """Find only the first bar's own explicit position, ignoring nested settings."""
    tokens = (token for token in _JSONC_TOKEN.finditer(content)
              if not token.group().startswith(("//", "/*")))
    stack = []
    bar_depth = None
    previous = ""
    for token in tokens:
        value = token.group()
        if value in ("{", "["):
            stack.append(value)
            if value == "{" and bar_depth is None:
                bar_depth = len(stack)
        elif value in ("}", "]"):
            if len(stack) == bar_depth and value == "}":
                break  # Never toggle a later bar if the first has no position.
            if stack:
                stack.pop()
        elif (len(stack) == bar_depth and value.startswith('"')
              and previous in ("{", ",") and json.loads(value) == "position"):
            colon = next(tokens, None)
            position = next(tokens, None)
            if (colon is not None and colon.group() == ":" and position is not None
                    and position.group().startswith('"') and json.loads(position.group()) in _OPPOSITE):
                return position
            raise ValueError("The first bar has an invalid position; expected top, bottom, left, or right.")
        previous = value
    raise ValueError("The first bar needs an explicit position in config.jsonc to toggle it.")


def _get_active_waybar_pids() -> list[int]:
    pids = []
    uid = os.getuid()
    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            try:
                if entry.stat().st_uid != uid:
                    continue
                proc = Path(entry.path)
                if (proc / "comm").read_bytes() != b"waybar\n":
                    continue
                state = (proc / "stat").read_bytes().rpartition(b")")[2].split()[0]
                if state not in (b"Z", b"X"):
                    pids.append(int(entry.name))
            except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError):
                continue  # Process exited or became unreadable during the scan.
    return pids


def _stop_waybars() -> None:
    """Wait on process descriptors, including after SIGKILL; never poll zombies."""
    poller = select.poll()
    descriptors = set()
    try:
        for pid in _get_active_waybar_pids():
            try:
                fd = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            descriptors.add(fd)
            # Verify identity after opening the descriptor, closing the scan/open race.
            try:
                if Path(f"/proc/{pid}/comm").read_bytes() != b"waybar\n":
                    descriptors.remove(fd)
                    os.close(fd)
                    continue
            except FileNotFoundError:
                descriptors.remove(fd)
                os.close(fd)
                continue
            poller.register(fd, select.POLLIN)
            try:
                signal.pidfd_send_signal(fd, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for timeout, sig in ((150, signal.SIGKILL), (1000, None)):
            deadline = time.monotonic() + timeout / 1000
            while descriptors:
                remaining = max(0, deadline - time.monotonic())
                for fd, _event in poller.poll(remaining * 1000):
                    poller.unregister(fd)
                    descriptors.remove(fd)
                    os.close(fd)
                if not descriptors or time.monotonic() >= deadline:
                    break
            if sig is not None:
                for fd in descriptors:
                    try:
                        signal.pidfd_send_signal(fd, sig)
                    except ProcessLookupError:
                        pass
        if descriptors:
            raise RuntimeError("Waybar did not exit after SIGKILL; restart aborted.")
    finally:
        for fd in descriptors:
            os.close(fd)


def _atomic_text(path: Path, content: str) -> None:
    """Replace a file without truncation, preserving existing permissions."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False,
                                     mode="w", encoding="utf-8", newline="") as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            if path.exists():
                os.fchmod(stream.fileno(), path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _atomic_symlink(path: Path, target: Path) -> None:
    # A private same-filesystem directory avoids collisions and stale temp links.
    with tempfile.TemporaryDirectory(dir=path.parent, prefix=".waybar-link-") as directory:
        temporary = Path(directory) / "link"
        temporary.symlink_to(target)
        os.replace(temporary, path)


@contextmanager
def _rollback_files(paths: list[Path]):
    """Restore the previous files if a configuration write fails midway."""
    with ExitStack() as stack:
        originals = []
        directories = []
        failures = []
        for path in dict.fromkeys(paths):
            directory = tempfile.TemporaryDirectory(dir=path.parent, prefix=".waybar-backup-", delete=False)
            directories.append(directory.name)
            # Keep recovery copies if restoring a file also fails.
            stack.callback(lambda directory=directory: directory.cleanup() if not failures else None)
            backup = Path(directory.name) / "original"
            if path.is_symlink() or path.exists():
                shutil.copy2(path, backup, follow_symlinks=False)
                originals.append((path, backup))
            else:
                originals.append((path, None))
        try:
            yield
        except BaseException as exc:
            for path, backup in reversed(originals):
                try:
                    if backup is None:
                        path.unlink(missing_ok=True)
                    else:
                        os.replace(backup, path)
                except OSError as restore_error:
                    failures.append(f"{path}: {restore_error}")
            if failures:
                raise RuntimeError(f"{exc}; could not restore: {'; '.join(failures)}. "
                                   f"Recovery copies retained in: {', '.join(directories)}") from exc
            raise


class WaybarEngine(BaseEngine):
    refresh_after_write = True

    def __init__(self, config_path: str = "~/.config/waybar"):
        path = Path(config_path).expanduser().absolute()
        # Resolve the directory, preserving the active configuration symlink itself.
        self.config_root = (path.parent if path.name == "config.jsonc" else path).resolve()
        self.config_path = self.config_root / "config.jsonc"
        self.style_path = self.config_root / "style.css"
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config").expanduser()
        self.state_file = config_home / "dusky/settings/waybar/.dusky_waybar_state.json"
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
        self.lock_file = runtime / "dusky_waybar_restart.lock"
        state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state").expanduser()
        self.log_file = state_home / "dusky/waybar.log"
        self.cache: dict[str, Any] = {}
        self.theme_dirs: list[Path] = []
        self.theme_names: list[str] = []
        self._mutex = threading.RLock()
        self._process: subprocess.Popen | None = None

    @property
    def target_path(self) -> str:
        # The TUI watches directory metadata, including replaced symlinks.
        return str(self.config_root)

    def _saved_index(self) -> int:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"),
                              object_pairs_hook=frozendict, array_hook=tuple)
        except (FileNotFoundError, UnicodeError, json.JSONDecodeError):
            return -1
        if not isinstance(data, frozendict):
            return -1
        name = data.get("active_theme_name")
        if name in self.theme_names:
            return self.theme_names.index(name)
        index = data.get("active_theme_index")
        return index if type(index) is int and 0 <= index < len(self.theme_names) else -1

    def load_state(self) -> dict[str, Any]:
        with self._mutex:
            self.theme_dirs = discover_themes(self.config_root)
            self.theme_names = [directory.name for directory in self.theme_dirs]
            index = -1
            if self.config_path.is_symlink():
                target = self.config_path.resolve()
                for i, directory in enumerate(self.theme_dirs):
                    if target == (directory / "config.jsonc").resolve():
                        index = i
                        break
            if index < 0:
                index = self._saved_index()
            number = index + 1 if index >= 0 else 1
            state = {
                "active_theme_index": index,
                "active_theme_name": self.theme_names[index] if index >= 0 else "unknown",
                "active_theme_number": number,
                "waybar": number,
                "action_invert_pos": False,
                "action_heal_state": False,
            }
            self.cache = state | {f"DEFAULT/{key}": value for key, value in state.items()}
            return self.cache

    def _restart_waybar(self, command: list[str]) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("w", encoding="utf-8") as log:
            _stop_waybars()
            if self._process is not None:
                self._process.poll()  # Reap our previous launcher when applicable.
            self._process = subprocess.Popen(command, start_new_session=True,
                                             stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            process = self._process
            confirmed = False
            try:
                # Both dusky-run (systemd-run --scope) and direct launch exec Waybar
                # in this PID. Check our child instead of scanning all processes.
                comm = Path(f"/proc/{process.pid}/comm")
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    code = process.poll()
                    if code is not None:
                        raise RuntimeError(f"Waybar launcher exited with status {code}; see {self.log_file}")
                    try:
                        started = comm.read_bytes() == b"waybar\n"
                    except FileNotFoundError:
                        continue  # Reap and report the exit on the next iteration.
                    # Python 3.15 waits on pidfds: exits wake us immediately.
                    try:
                        code = process.wait(timeout=0.1 if started else 0.01)
                    except subprocess.TimeoutExpired:
                        if started:
                            # Keep child ownership until exit, including after engine disposal.
                            threading.Thread(target=process.wait, daemon=True).start()
                            confirmed = True
                            return
                    else:
                        raise RuntimeError(f"Waybar launcher exited with status {code}; see {self.log_file}")
                raise RuntimeError(f"Waybar did not start within 2 seconds; see {self.log_file}")
            finally:
                if not confirmed and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    def write_value(self, target_key: str, target_scope: str, new_value: str,
                    item_type: str = "string") -> WriteResult:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[Change]) -> WriteResult:
        if not changes:
            return True, "No changes requested.", ""
        try:
            with self._mutex, self.lock_file.open("a", encoding="utf-8") as lock:
                # Serialize selection, file writes, termination, and confirmed launch.
                fcntl.flock(lock, fcntl.LOCK_EX)
                return self._write_locked(changes)
        except (OSError, ValueError, RuntimeError) as exc:
            # Reflect actual disk state after a failed or partially completed operation.
            try:
                self.load_state()
            except OSError:
                self.cache = {}
            return False, str(exc), ""

    def _write_locked(self, changes: list[Change]) -> WriteResult:
        self.load_state()
        if not self.theme_dirs:
            raise ValueError(f"No valid themes found in {self.config_root}")
        index = self.cache["active_theme_index"]
        edits: dict[Path, str] = {}
        restart = False
        message = ""
        for key, scope, value, _kind in changes:
            if scope != "DEFAULT":
                raise ValueError(f"Unsupported Waybar scope: {scope}")
            match key:
                case "active_theme_number" | "waybar" | "active_theme_index":
                    number = int(value)
                    index = number if key == "active_theme_index" else number - 1
                case "active_theme_name":
                    if value in self.theme_names:
                        index = self.theme_names.index(value)
                    else:
                        try:
                            index = int(value) - 1
                        except ValueError:
                            raise ValueError(f"Theme {value!r} was not found.") from None
                case "toggle_forward" | "toggle_backward" | "action_invert_pos" | "action_heal_state":
                    if str(value).lower() == "false":
                        continue
                    if str(value).lower() != "true":
                        raise ValueError(f"Invalid trigger value: {value}")
                    if key == "toggle_forward":
                        index = (index + 1) % len(self.theme_dirs)
                    elif key == "toggle_backward":
                        index = (index - 1) % len(self.theme_dirs) if index >= 0 else len(self.theme_dirs) - 1
                    elif key == "action_heal_state":
                        saved = self._saved_index()
                        index = saved if saved >= 0 else max(index, 0)
                        message = "Configuration links restored."
                    else:
                        index = max(index, 0)
                        path = (self.theme_dirs[index] / "config.jsonc").resolve()
                        content = edits[path] if path in edits else path.read_text(encoding="utf-8", newline="")
                        token = _position_token(content)
                        position = _OPPOSITE[json.loads(token.group())]
                        edits[path] = content[:token.start()] + json.dumps(position) + content[token.end():]
                        message = f"First bar position changed to {position}."
                case _:
                    raise ValueError(f"Unsupported Waybar setting: {key}")
            if not 0 <= index < len(self.theme_dirs):
                raise ValueError(f"Theme index {index} is out of bounds (1–{len(self.theme_dirs)} for theme numbers).")
            restart = True
        if not restart:
            return True, "No changes requested.", ""

        waybar = shutil.which("waybar")
        if waybar is None:
            raise RuntimeError("Waybar executable was not found in PATH.")
        if not os.environ.get("WAYLAND_DISPLAY"):
            raise RuntimeError("A running Wayland session is required to restart Waybar.")
        launcher = shutil.which("dusky-run")
        command = ([launcher, waybar] if launcher else [waybar]) + ["--config", str(self.config_path)]
        directory = self.theme_dirs[index]
        style = directory / "style.css"
        if style.is_file():
            command.extend(["--style", str(self.style_path)])
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        # Validate the whole batch, then back up files before any mutation.
        with _rollback_files([*edits, self.config_path, self.style_path, self.state_file]):
            for path, content in edits.items():
                _atomic_text(path, content)
            _atomic_symlink(self.config_path, directory / "config.jsonc")
            if style.is_file():
                _atomic_symlink(self.style_path, style)
            else:
                self.style_path.unlink(missing_ok=True)
            _atomic_text(self.state_file, json.dumps({"active_theme_name": directory.name,
                                                    "active_theme_index": index}, indent=2) + "\n")
            os.utime(self.config_root, None)
        self.load_state()
        try:
            self._restart_waybar(command)
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(f"Configuration saved, but Waybar restart failed: {exc}") from exc
        return True, message or f"Applied theme: {directory.name}", ""
