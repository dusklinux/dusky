#!/usr/bin/env python3
"""Linux Codex account manager, using Python 3.15+ and the standard library.

Backups contain full credentials, including unknown auth.json fields. Backup,
restore, import, quota inspection and adding a login preserve live auth.json.
Only an explicitly selected switch replaces it. Codex owns OAuth refresh; this
program never consumes refresh tokens. See switch_accounts.md for usage and
recovery semantics.
"""

import argparse
import base64
import contextlib
lazy import ctypes
from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
from functools import cache
import hashlib
lazy import http.client
import json
import os
from pathlib import Path
import re
lazy import select
lazy import shutil
lazy import signal
import stat
lazy import subprocess
import sys
import tempfile
lazy import time
lazy import tomllib
lazy import urllib.error
lazy import urllib.request

NAME_REGEX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
BACKUP_FORMAT = "codex-account-switcher"
BACKUP_VERSION = 1
QUOTA_HEADERS = frozendict({
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://chatgpt.com",
    "Referer": "https://chatgpt.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    # Preserve the reference client's browser headers for this private endpoint.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
})


class SwitchError(Exception):
    """An actionable account-manager error, without credential contents."""


def home() -> Path:
    path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    if not path.is_absolute():
        raise SwitchError("CODEX_HOME must be an absolute path")
    return path


def check_name(name: str) -> str:
    name = name.strip()
    if not NAME_REGEX.fullmatch(name):
        raise SwitchError("Use 1–64 letters, digits, dots, dashes or underscores; start with a letter or digit")
    return name


def read_regular(path: Path) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise SwitchError(f"Expected a regular file: {path}")
    return path.read_bytes()


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@cache
def rename_function():
    """Linux libc interface: atomic publication without replacing a destination."""
    function = ctypes.CDLL(None, use_errno=True).renameat2
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    return function


def rename_without_replace(source: Path, destination: Path) -> None:
    # AT_FDCWD = -100; RENAME_NOREPLACE = 1 (documented Linux interfaces).
    if rename_function()(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def unlink_owned(path: Path, file_id: tuple[int, int]) -> None:
    """Remove a file only while it still belongs to the operation being undone."""
    with contextlib.suppress(FileNotFoundError):
        current = path.lstat()
        if (current.st_dev, current.st_ino) == file_id:
            path.unlink()


def atomic_write(path: Path, data: bytes, *, replace: bool = True) -> tuple[int, int]:
    """Publish a complete mode-0600 file; preserve existing parent permissions."""
    if path.is_symlink():
        raise SwitchError(f"Expected a regular file: {path}")
    path.parent.mkdir(mode=0o700, parents=True, parent_mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".switch-", dir=path.parent)
    temp = Path(temporary)
    info = os.fstat(fd)
    file_id = (info.st_dev, info.st_ino)
    published = False
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if replace:
            os.replace(temp, path)
        else:
            rename_without_replace(temp, path)
        published = True
        sync_directory(path.parent)
        return file_id
    except BaseException as exc:
        if published and not replace:
            # Only undo our own exclusive publication, never a concurrent writer's file.
            unlink_owned(path, file_id)
        if published and replace and isinstance(exc, OSError):
            raise SwitchError(
                f"Updated {path}, but disk synchronization failed; "
                "the complete new file is already in place"
            ) from exc
        raise
    finally:
        temp.unlink(missing_ok=True)


def encode_json(value: object) -> bytes:
    try:
        return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise SwitchError("Cannot encode valid JSON") from exc


def unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SwitchError("JSON contains duplicate keys")
        result[key] = value
    return result


def decode_json(data: bytes | None) -> object:
    if not data:
        raise SwitchError("File is missing or empty")
    try:
        def invalid_constant(value: str) -> None:
            raise SwitchError("JSON contains a non-finite number")

        return json.loads(data, object_pairs_hook=unique_keys, parse_constant=invalid_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise SwitchError("Invalid JSON file") from exc


def parse_auth(data: bytes | None) -> dict:
    value = decode_json(data)
    if not isinstance(value, dict):
        raise SwitchError("Credentials must be a JSON object")
    tokens = value.get("tokens")
    if isinstance(tokens, dict) and all(
        isinstance(tokens.get(key), str) and tokens[key].strip()
        for key in ("access_token", "refresh_token", "id_token")
    ):
        return value
    key = value.get("OPENAI_API_KEY")
    if tokens is None and isinstance(key, str) and key.strip():
        return value
    raise SwitchError("Credentials need ChatGPT ID/access/refresh tokens or an OPENAI_API_KEY")


def parse_jwt_claims(token: str | None) -> dict:
    if not isinstance(token, str) or len(parts := token.split(".")) != 3:
        return {}
    try:
        value = json.loads(base64.urlsafe_b64decode(parts[1]))
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeDecodeError, RecursionError):
        return {}


def identity(auth: dict) -> str:
    tokens = auth.get("tokens")
    if isinstance(tokens, dict) and tokens.get("id_token"):
        claims = parse_jwt_claims(tokens["id_token"])
        details = claims.get("https://api.openai.com/auth")
        details = details if isinstance(details, dict) else {}
        account = tokens.get("account_id") or details.get("chatgpt_account_id")
        user = claims.get("sub") or details.get("chatgpt_user_id") or claims.get("email")
        if account or user:
            # Workspace alone can be shared by several different users.
            return "chatgpt:" + json.dumps([account, user], sort_keys=True)
        return "id:" + hashlib.sha256(tokens["id_token"].encode()).hexdigest()
    if key := auth.get("OPENAI_API_KEY"):
        return "api:" + hashlib.sha256(key.encode()).hexdigest()
    raise SwitchError("Cannot determine account identity")


def file_store_update(codex_home: Path) -> bytes | None:
    """Validate the store and prepare a configuration update before switching."""
    path = codex_home / "config.toml"
    data = read_regular(path)
    try:
        config = tomllib.loads(data.decode("utf-8")) if data is not None else {}
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SwitchError("Cannot parse config.toml") from exc
    store = config.get("cli_auth_credentials_store")
    if store == "file":
        return None
    if store is not None:
        raise SwitchError(f'Credential store is {store!r}; set cli_auth_credentials_store = "file" before switching')
    return b'cli_auth_credentials_store = "file"\n' + (data or b"")


@dataclass(frozen=True, slots=True)
class CodexProcess:
    pid: int
    started: str
    background: bool = False


def process_started(path: Path) -> str:
    # comm (field 2) can contain spaces and parentheses. starttime is field 22.
    return (path / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[19]


def classify_codex_processes(codex_home: Path) -> list[CodexProcess]:
    """Find this user's Codex processes using the Linux procfs interface."""
    result = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit() or int(path.name) == os.getpid():
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
            started = process_started(path)
            args = tuple(os.fsdecode(part) for part in (path / "cmdline").read_bytes().split(b"\0") if part)
            if not args:
                continue
            executable = Path(args[0]).name
            name = (path / "comm").read_text(encoding="utf-8").strip()
            codex_names = {"codex", "codex-cli", "codex-linux"}
            is_codex = name in codex_names or executable in codex_names
            if executable in {"node", "nodejs"} and len(args) > 1:
                is_codex = Path(args[1]).name == "codex.js"
            if not is_codex:
                continue
            env = dict(item.split(b"=", 1) for item in (path / "environ").read_bytes().split(b"\0") if b"=" in item)
            process_home = Path(os.fsdecode(env.get(b"CODEX_HOME") or os.fsencode(Path.home() / ".codex")))
            if process_home.resolve() != codex_home.resolve():
                continue
            command_args = args[2:] if executable in {"node", "nodejs"} else args[1:]
            result.append(CodexProcess(int(path.name), started, command_args[:1] == ("app-server",)))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as exc:
            raise SwitchError(f"Cannot inspect process {path.name}; use --force only if you intend to bypass checks") from exc
    return result


def close_processes(processes: list[CodexProcess]) -> None:
    """Close explicitly selected processes and wait for exit."""
    ancestors = set()
    parent = os.getppid()
    while parent > 1 and parent not in ancestors:
        ancestors.add(parent)
        try:
            info = (Path("/proc") / str(parent) / "stat").read_text(encoding="utf-8")
            parent = int(info.rsplit(")", 1)[1].split()[1])
        except FileNotFoundError:
            break
    if any(proc.pid in ancestors for proc in processes):
        raise SwitchError("Cannot close the Codex session running this command; switch from a separate terminal")
    with contextlib.ExitStack() as stack:
        poller = select.poll()
        descriptors = []
        for proc in processes:
            try:
                fd = os.pidfd_open(proc.pid)
                stack.callback(os.close, fd)
                if process_started(Path("/proc") / str(proc.pid)) != proc.started:
                    continue
                signal.pidfd_send_signal(fd, signal.SIGTERM)
                poller.register(fd, select.POLLIN)
                descriptors.append(fd)
            except (FileNotFoundError, ProcessLookupError):
                continue
        deadline = time.monotonic() + 3
        alive = set(descriptors)
        while alive and (remaining := deadline - time.monotonic()) > 0:
            for fd, _ in poller.poll(max(1, int(remaining * 1000))):
                alive.discard(fd)
                poller.unregister(fd)
        for fd in alive:
            with contextlib.suppress(ProcessLookupError):
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        deadline = time.monotonic() + 3
        while alive and (remaining := deadline - time.monotonic()) > 0:
            for fd, _ in poller.poll(max(1, int(remaining * 1000))):
                alive.discard(fd)
                poller.unregister(fd)
        if alive:
            raise SwitchError("Some Codex processes did not exit; switch aborted")


class CodexProfileManager:
    def __init__(self, force_mode: bool = False, restart_mode: bool = False, codex_home: Path | None = None) -> None:
        self.force_mode = force_mode
        self.restart_mode = restart_mode
        self.codex_home = (codex_home or home()).expanduser().absolute()
        self.root = self.codex_home / "account-switcher"
        self.profiles_dir = self.root / "profiles"
        self.active_file = self.root / "active"
        self.order_file = self.root / "order.txt"
        self.lock_file = self.root / ".lock"

    @contextlib.contextmanager
    def locked(self):
        self.profiles_dir.mkdir(mode=0o700, parents=True, parent_mode=0o700, exist_ok=True)
        with self.lock_file.open("a+b") as output:
            os.fchmod(output.fileno(), 0o600)
            fcntl.flock(output, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(output, fcntl.LOCK_UN)

    def get_profile_path(self, name: str) -> Path:
        return self.profiles_dir / f"{check_name(name)}.json"

    def _read_order(self) -> list[str]:
        data = read_regular(self.order_file)
        return list(dict.fromkeys(data.decode("utf-8").splitlines())) if data else []

    def _persist_order(self, names: list[str]) -> None:
        atomic_write(self.order_file, ("\n".join(names) + "\n").encode("utf-8"))

    def get_all(self) -> list[str]:
        names = {check_name(path.stem) for path in self.profiles_dir.glob("*.json")}
        ordered = [name for name in self._read_order() if name in names]
        return ordered + sorted(names.difference(ordered))

    def read_profile_auth(self, name: str) -> dict:
        try:
            return parse_auth(read_regular(self.get_profile_path(name)))
        except SwitchError as exc:
            raise SwitchError(f"Profile {name!r}: {exc}") from exc

    def live_auth(self) -> tuple[bytes | None, dict | None]:
        data = read_regular(self.codex_home / "auth.json")
        return data, parse_auth(data) if data is not None else None

    def get_active(self) -> str | None:
        _, live = self.live_auth()
        if live is None:
            return None
        live_id = identity(live)
        marker = read_regular(self.active_file)
        preferred = marker.decode("utf-8").strip() if marker else None
        names = self.get_all()
        if preferred in names:
            names.remove(preferred)
            names.insert(0, preferred)
        for name in names:
            try:
                stored = self.read_profile_auth(name)
            except SwitchError:
                continue
            if identity(stored) == live_id:
                return name
        return None

    def set_active(self, name: str) -> None:
        atomic_write(self.active_file, (check_name(name) + "\n").encode("utf-8"))

    def unique_name(self, name: str, existing: set[str]) -> str:
        candidate = name
        number = 2
        while candidate in existing:
            suffix = f"-{number}"
            candidate = name[:64 - len(suffix)] + suffix
            number += 1
        return candidate

    def sync_active_tokens(self, *, preserve_unsaved: bool = False) -> None:
        """Caller holds the lock. Synchronize every alias, without suppressing I/O errors."""
        data, live = self.live_auth()
        if live is None:
            return
        live_id = identity(live)
        names = self.get_all()
        matched = False
        for name in names:
            try:
                stored = self.read_profile_auth(name)
            except SwitchError:
                continue
            if identity(stored) == live_id:
                path = self.get_profile_path(name)
                if read_regular(path) != data:
                    atomic_write(path, data)
                matched = True
        if preserve_unsaved and not matched:
            name = self.unique_name("current-login", set(names))
            atomic_write(self.get_profile_path(name), data, replace=False)
            self._persist_order(names + [name])
            print(f"Saved previously unsaved login as {name!r}.")

    def resolve_target(self, name: str) -> str:
        names = self.get_all()
        if name in names:
            return name
        if name.isdecimal() and 1 <= int(name) <= len(names):
            return names[int(name) - 1]
        check_name(name)
        raise SwitchError(f"No saved account {name!r}; use --list")

    def switch(self, name: str) -> bool:
        with self.locked():
            name = self.resolve_target(name.strip())
            target_data = read_regular(self.get_profile_path(name))
            target_auth = parse_auth(target_data)
            _, live = self.live_auth()
            if live is not None and identity(live) == identity(target_auth):
                self.sync_active_tokens()
                self.set_active(name)
                print(f"Already signed in as {name!r}.")
                return True
            config_update = file_store_update(self.codex_home)
            processes = [] if self.force_mode else classify_codex_processes(self.codex_home)
            if processes and self.restart_mode:
                close_processes(processes)
            elif processes and not self.force_mode:
                pids = ", ".join(str(process.pid) for process in processes)
                if not sys.stdin.isatty():
                    raise SwitchError(
                        f"Codex is running (PIDs: {pids}). "
                        "Close it, or use --restart / --force explicitly"
                    )
                print(f"\nCodex is running (PIDs: {pids}). Switch to {name!r}?")
                print("  1. Close Codex and switch (start Codex again afterward)")
                print("  2. Switch without closing (running sessions may retain their account)")
                print("  3. Cancel")
                while True:
                    answer = input("Choice [3]: ").strip().lower()
                    if answer in {"", "3", "cancel", "q"}:
                        print("Switch cancelled; current login preserved.")
                        return False
                    if answer in {"1", "close", "restart"}:
                        close_processes(processes)
                        break
                    if answer in {"2", "ignore", "force"}:
                        print("Running sessions may retain their existing account.", file=sys.stderr)
                        break
                    print("Enter 1, 2 or 3; blank cancels.")
            elif self.force_mode:
                print("Continuing with --force; running sessions may retain their existing account.", file=sys.stderr)
            self.sync_active_tokens(preserve_unsaved=True)
            if config_update is not None:
                atomic_write(self.codex_home / "config.toml", config_update)
            # No OAuth call here: Codex refreshes its own credentials after startup.
            atomic_write(self.codex_home / "auth.json", target_data)
            self.set_active(name)
            print(f"Switched to {name!r}. Start Codex to use this account.")
        return True

    def close_background_helpers(self) -> None:
        """Explicit menu action; the owning application must restart its helper."""
        with self.locked():
            helpers = [process for process in classify_codex_processes(self.codex_home) if process.background]
            if not helpers:
                print("No Codex app-server helpers are running for this home.")
                return
            close_processes(helpers)
        print("Closed Codex app-server helpers. Reopen Codex or its extension to reload credentials.")

    def cycle_next(self) -> bool:
        names = self.get_all()
        if not names:
            raise SwitchError("No saved accounts")
        active = self.get_active()
        return self.switch(names[(names.index(active) + 1) % len(names)] if active in names else names[0])

    def save_current(self, name: str) -> bool:
        name = check_name(name)
        with self.locked():
            data, auth = self.live_auth()
            if auth is None:
                raise SwitchError("No auth.json login to save")
            path = self.get_profile_path(name)
            existing = read_regular(path)
            if existing is not None and identity(parse_auth(existing)) != identity(auth):
                raise SwitchError(f"{name!r} belongs to another account; choose another name")
            names = self.get_all()
            atomic_write(path, data)
            self.set_active(name)
            self._persist_order(list(dict.fromkeys(names + [name])))
        print(f"Saved current login as {name!r}.")
        return True

    def import_auth(self, source_path: Path, name: str, switch_now: bool = False) -> bool:
        name = check_name(name)
        data = read_regular(source_path.expanduser())
        parse_auth(data)
        with self.locked():
            names = self.get_all()
            atomic_write(self.get_profile_path(name), data, replace=False)
            self._persist_order(names + [name])
        print(f"Imported {name!r}; current login preserved.")
        # Never acquire a second flock while holding the first one.
        return self.switch(name) if switch_now else True

    def login_account(self, name: str, device_auth: bool = False) -> bool:
        name = check_name(name)
        codex_bin = shutil.which("codex")
        if codex_bin is None:
            raise SwitchError("codex is not available in PATH")
        if self.get_profile_path(name).exists():
            raise SwitchError(f"Account {name!r} already exists")
        with tempfile.TemporaryDirectory(prefix="codex-login-") as temporary:
            isolated_home = Path(temporary)
            command = [codex_bin, "login", "-c", 'cli_auth_credentials_store="file"']
            if device_auth:
                command.append("--device-auth")
            env = dict(os.environ, CODEX_HOME=str(isolated_home))
            print(f"Signing in for {name!r}; current Codex login stays in place.", flush=True)
            result = subprocess.run(command, env=env, check=False)
            if result.returncode:
                raise SwitchError(f"Codex login exited with status {result.returncode}")
            self.import_auth(isolated_home / "auth.json", name)
        return True

    def backup(self, destination: Path) -> Path:
        destination = destination.expanduser().absolute()
        if destination.is_dir():
            destination /= f"codex-accounts-{datetime.now(UTC):%Y%m%dT%H%M%S.%fZ}.json"
        with self.locked():
            names = self.get_all()
            profiles = {name: self.read_profile_auth(name) for name in names}
            _, live = self.live_auth()
            active = None
            if live is not None:
                matched = []
                for name, auth in profiles.items():
                    if identity(auth) == identity(live):
                        profiles[name] = live
                        matched.append(name)
                if matched:
                    marker = read_regular(self.active_file)
                    preferred = marker.decode("utf-8").strip() if marker else None
                    active = preferred if preferred in matched else matched[0]
                else:
                    active = self.unique_name("current-login", set(names))
                    profiles[active] = live
            if not profiles:
                raise SwitchError("No credentials to back up")
            archive = {
                "format": BACKUP_FORMAT,
                "version": BACKUP_VERSION,
                "created_at": datetime.now(UTC).isoformat(),
                "active": active,
                "accounts": [{"name": name, "auth": auth} for name, auth in profiles.items()],
            }
            # Refuse to publish an archive into the managed profile namespace.
            resolved = destination.resolve()
            if resolved.is_relative_to(self.root.resolve()) or resolved in {
                (self.codex_home / "auth.json").resolve(),
                (self.codex_home / "config.toml").resolve(),
            }:
                raise SwitchError("Choose a backup destination outside the managed account/config files")
            atomic_write(destination, encode_json(archive), replace=False)
        print(f"Backed up {len(profiles)} account(s) to {destination}")
        mode = stat.S_IMODE(destination.stat().st_mode)
        print(f"This file contains full, unencrypted credentials (file mode {mode:04o}).")
        return destination

    def restore(self, source: Path) -> bool:
        archive = decode_json(read_regular(source.expanduser()))
        if not isinstance(archive, dict) or archive.get("format") != BACKUP_FORMAT:
            raise SwitchError("Not a switcher backup; use --import-file NAME PATH for a single auth.json")
        if type(archive.get("version")) is not int or archive["version"] != BACKUP_VERSION:
            raise SwitchError("Unsupported backup version")
        accounts = archive.get("accounts")
        if not isinstance(accounts, list) or not accounts:
            raise SwitchError("Backup has no accounts")
        validated = {}
        for entry in accounts:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                raise SwitchError("Invalid backup account entry")
            name = check_name(entry["name"])
            if name in validated:
                raise SwitchError("Backup contains duplicate account names")
            validated[name] = parse_auth(encode_json(entry.get("auth")))
            identity(validated[name])
        active = archive.get("active")
        if active is not None and (not isinstance(active, str) or active not in validated):
            raise SwitchError("Backup references a missing active account")
        with self.locked():
            names = self.get_all()
            known = set()
            for name in names:
                try:
                    known.add(identity(self.read_profile_auth(name)))
                except SwitchError:
                    # Keep malformed files intact; recovered accounts get another name.
                    continue
            _, live = self.live_auth()
            live_id = identity(live) if live is not None else None
            additions = {}
            skipped = 0
            for original, auth in validated.items():
                account_id = identity(auth)
                if account_id in known:
                    skipped += 1
                    continue
                name = self.unique_name(original, set(names) | set(additions))
                # Never restore an old refresh token over this machine's current login.
                additions[name] = live if account_id == live_id else auth
                known.add(account_id)
                if name != original:
                    print(f"Name {original!r} is in use; restoring as {name!r}.")
            previous_order = read_regular(self.order_file)
            written = []
            try:
                for name, auth in additions.items():
                    path = self.get_profile_path(name)
                    file_id = atomic_write(path, encode_json(auth), replace=False)
                    written.append((path, file_id))
                if additions:
                    self._persist_order(names + list(additions))
            except BaseException:
                # Normal errors and Ctrl-C roll back additions; existing profiles stay intact.
                for path, file_id in written:
                    unlink_owned(path, file_id)
                if previous_order is None:
                    self.order_file.unlink(missing_ok=True)
                else:
                    atomic_write(self.order_file, previous_order)
                sync_directory(self.profiles_dir)
                raise
        print(f"Restored {len(additions)} account(s); skipped {skipped} already saved account(s). Current login preserved.")
        return True

    def delete_profile(self, name: str) -> bool:
        name = check_name(name)
        with self.locked():
            name = self.resolve_target(name)
            if name == self.get_active():
                raise SwitchError("Cannot delete the active profile; switch first")
            names = self.get_all()
            self.get_profile_path(name).unlink()
            self._persist_order([item for item in names if item != name])
        print(f"Deleted {name!r}.")
        return True

    def rename_profile(self, old_name: str, new_name: str) -> bool:
        new_name = check_name(new_name)
        with self.locked():
            old_name = self.resolve_target(old_name.strip())
            if old_name == new_name:
                return True
            names = self.get_all()
            active = self.get_active()
            old = self.get_profile_path(old_name)
            new = self.get_profile_path(new_name)
            if not stat.S_ISREG(old.lstat().st_mode):
                raise SwitchError(f"Expected a regular file: {old}")
            rename_without_replace(old, new)
            try:
                sync_directory(self.profiles_dir)
                if active == old_name:
                    self.set_active(new_name)
                self._persist_order([new_name if name == old_name else name for name in names])
            except (OSError, SwitchError) as exc:
                raise SwitchError(
                    f"Account is now named {new_name!r}, but synchronization or metadata "
                    "update failed; its credentials remain in the new file"
                ) from exc
        print(f"Renamed {old_name!r} to {new_name!r}.")
        return True

    def reorder_profile(self, name: str, direction: str) -> bool:
        with self.locked():
            name = self.resolve_target(name.strip())
            names = self.get_all()
            index = names.index(name)
            if direction not in {"up", "down"}:
                raise SwitchError("Direction must be up or down")
            target = index + (-1 if direction == "up" else 1)
            if not 0 <= target < len(names):
                return False
            names[index], names[target] = names[target], names[index]
            self._persist_order(names)
        return True

    def render_dashboard(self) -> None:
        names = self.get_all()
        active = self.get_active()
        if not names:
            print("No saved accounts. Use --save NAME or --login NAME.")
            return
        _, live = self.live_auth()
        rows = [("#", "State", "Account", "Plan", "Access token", "Email / ID")]
        for number, name in enumerate(names, 1):
            try:
                auth = self.read_profile_auth(name)
            except SwitchError:
                rows.append((str(number), "INVALID", name, "—", "—", "Repair or delete this profile"))
                continue
            if name == active and live is not None:
                auth = live
            tokens = auth.get("tokens")
            if isinstance(tokens, dict) and tokens.get("id_token"):
                claims = parse_jwt_claims(tokens["id_token"])
                details = claims.get("https://api.openai.com/auth")
                details = details if isinstance(details, dict) else {}
                plan = str(details.get("chatgpt_plan_type") or "ChatGPT")
                email = str(claims.get("email") or tokens.get("account_id") or "Unknown")
                expiry = parse_jwt_claims(tokens.get("access_token")).get("exp")
                token_state = "unknown expiry"
                if type(expiry) in {int, float}:
                    token_state = "expired" if expiry <= time.time() else "not expired"
            else:
                plan, email = "API key", "OpenAI Platform"
                token_state = "—"
            rows.append((str(number), "ACTIVE" if name == active else "saved", name, plan, token_state, email))
        widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
        for row in rows:
            print("  ".join(value.ljust(width) for value, width in zip(row, widths)))

    def check_quota(self, name: str) -> bool:
        with self.locked():
            name = self.resolve_target(name.strip())
            auth = self.read_profile_auth(name)
            _, live = self.live_auth()
            if live is not None and identity(auth) == identity(live):
                auth = live
        tokens = auth.get("tokens")
        if not isinstance(tokens, dict):
            raise SwitchError("ChatGPT quota is unavailable for API-key accounts")
        headers = {**QUOTA_HEADERS, "Authorization": f"Bearer {tokens['access_token']}"}
        claims = parse_jwt_claims(tokens.get("id_token"))
        details = claims.get("https://api.openai.com/auth")
        account = tokens.get("account_id") or (details.get("chatgpt_account_id") if isinstance(details, dict) else None)
        if account:
            headers["ChatGPT-Account-Id"] = str(account)
        request = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                usage = decode_json(response.read())
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 401:
                raise SwitchError(
                    "Quota access token expired or was rejected. Let Codex refresh "
                    "that account, then retry; current login preserved"
                ) from exc
            raise SwitchError(f"Quota endpoint returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException) as exc:
            raise SwitchError("Quota endpoint unavailable; retry later") from exc
        if not isinstance(usage, dict):
            raise SwitchError("Unexpected quota response")
        print(f"Quota for {name!r} — plan: {usage.get('plan_type', 'Unknown')}")
        limits = usage.get("rate_limit")
        if not isinstance(limits, dict):
            raise SwitchError("Quota response has no rate-limit data")
        if limits.get("limit_reached") is True or limits.get("allowed") is False:
            print("  Usage blocked: rate limit reached or access unavailable.")
        elif limits.get("allowed") is True:
            print("  Usage allowed.")
        for key, fallback in (("primary_window", "Primary window"), ("secondary_window", "Secondary window")):
            window = limits.get(key)
            if not isinstance(window, dict):
                continue
            seconds = window.get("limit_window_seconds")
            label = f"{seconds / 3600:g}-hour window" if isinstance(seconds, (int, float)) and seconds > 0 else fallback
            reset = window.get("reset_at")
            try:
                reset_text = (
                    datetime.fromtimestamp(reset, UTC).astimezone().strftime("%b %d, %H:%M")
                    if isinstance(reset, (int, float)) else "unknown"
                )
            except (ValueError, OverflowError, OSError):
                reset_text = "unknown"
            print(f"  {label}: {window.get('used_percent', 'unknown')}% used; resets {reset_text}")
        credits = usage.get("credits")
        if isinstance(credits, dict):
            print(f"  Credits: {credits.get('balance', 'unknown')}")
        return True


def offer_switch(manager: CodexProfileManager, name: str) -> None:
    """Offer an explicit switch after saving a separate account in a terminal."""
    if sys.stdin.isatty() and input(f"Switch to {name!r} now? [y/N]: ").strip().lower() in {"y", "yes"}:
        manager.switch(name)


def interactive_tui(manager: CodexProfileManager) -> None:
    actions = [
        "Switch account", "Next account", "Check quota", "Save current login",
        "Login and save another account", "Import auth.json", "Back up all accounts",
        "Restore backup", "Rename account", "Reorder accounts", "Delete account",
        "Close background helpers to reload credentials",
    ]
    while True:
        print("\nCodex Account Manager\n")
        manager.render_dashboard()
        print()
        for index, label in enumerate(actions, 1):
            print(f"  {index:2}. {label}")
        print("   0. Quit\n")
        try:
            action = input("Action [0]: ").strip()
            if action in {"", "0", "q", "quit"}:
                return
            if action in {"1", "3", "9", "10", "11"}:
                target = input("Account name or number (blank cancels): ").strip()
                if not target:
                    continue
                target = manager.resolve_target(target)
            match action:
                case "1":
                    manager.switch(target)
                case "2":
                    manager.cycle_next()
                case "3":
                    manager.check_quota(target)
                case "4" | "5":
                    name = input("Account name (blank cancels): ").strip()
                    if name:
                        if action == "4":
                            manager.save_current(name)
                        else:
                            device = input("Use device code instead of browser? [y/N]: ").strip().lower() == "y"
                            manager.login_account(name, device)
                            offer_switch(manager, name)
                case "6":
                    source = input("Path to auth.json (blank cancels): ").strip()
                    if source:
                        name = input("Account name (blank cancels): ").strip()
                        if name:
                            manager.import_auth(Path(source), name)
                            offer_switch(manager, name)
                case "7" | "8":
                    prompt = "Backup file or existing directory" if action == "7" else "Backup file to restore"
                    source = input(f"{prompt} (blank cancels): ").strip()
                    if source:
                        if action == "7":
                            manager.backup(Path(source))
                        else:
                            manager.restore(Path(source))
                case "9":
                    name = input("New account name (blank cancels): ").strip()
                    if name:
                        manager.rename_profile(target, name)
                case "10":
                    while True:
                        direction = input(f"Move {target!r} up or down (blank finishes): ").strip()
                        if not direction:
                            break
                        manager.reorder_profile(target, direction)
                        manager.render_dashboard()
                case "11":
                    if input(f"Delete saved account {target!r}? [y/N]: ").strip().lower() == "y":
                        manager.delete_profile(target)
                case "12":
                    manager.close_background_helpers()
                case _:
                    print("Enter an action number from the menu.")
        except (SwitchError, OSError, UnicodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\nCancelled; current login preserved unless a switch already completed.")


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Preserve existing flag and subcommand spellings, plus backup / restore PATH.
    aliases = {
        "switch": "", "use": "", "list": "--list", "current": "--current",
        "save": "--save", "login": "--login", "import": "--import-file",
        "check": "--check", "backup": "--backup", "restore": "--restore",
    }
    # Global options can precede subcommands, just as they can precede action flags.
    index = 0
    while index < len(raw):
        option = raw[index]
        if option == "--home":
            index += 2
        elif option.startswith("--home=") or option in {"-f", "--force", "-r", "--restart", "--device-auth"}:
            index += 1
        else:
            break
    subcommand = raw[index] if index < len(raw) and raw[index] in aliases else None
    if subcommand is not None:
        replacement = aliases[subcommand]
        raw[index:index + 1] = [replacement] if replacement else []
    parser = argparse.ArgumentParser(
        description="Manage Codex accounts. Only an explicit switch changes the live login.",
        epilog="Examples: --backup ~/accounts.json | --restore ~/accounts.json | --home /path/to/.codex --restore ~/accounts.json",
        allow_abbrev=False,
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("target", nargs="?", help="Account name or number to switch to")
    actions.add_argument("-l", "--list", action="store_true", help="List saved accounts")
    actions.add_argument("--current", action="store_true", help="Print saved account matching the live login")
    actions.add_argument("-n", "--next", action="store_true", help="Switch to the next account")
    actions.add_argument("-c", "--check", nargs="?", const="", metavar="ACCOUNT", help="Inspect quota without refreshing credentials (default: active)")
    actions.add_argument("--save", metavar="NAME", help="Save the current login")
    actions.add_argument("--login", metavar="NAME", help="Login in an isolated home and save; offers a switch in a terminal")
    actions.add_argument("--import-file", nargs=2, metavar=("NAME", "PATH"), help="Import auth.json; offers a switch in a terminal")
    actions.add_argument("--backup", metavar="PATH", help="Back up all credentials to a new JSON file or existing directory")
    actions.add_argument("--restore", metavar="PATH", help="Merge a backup without overwriting saved accounts or the live login")
    actions.add_argument("--rename", nargs=2, metavar=("OLD", "NEW"), help="Rename a saved account")
    actions.add_argument("--delete", metavar="ACCOUNT", help="Delete a saved inactive account")
    actions.add_argument("--move", nargs=2, metavar=("ACCOUNT", "DIRECTION"), help="Move an account up or down")
    behavior = parser.add_mutually_exclusive_group()
    behavior.add_argument("-f", "--force", action="store_true", help="Switch while Codex runs; running sessions may retain old credentials")
    behavior.add_argument("-r", "--restart", action="store_true", help="Close Codex processes before switching; start Codex again yourself")
    parser.add_argument("--device-auth", action="store_true", help="Use device code with --login")
    parser.add_argument("--home", type=Path, help="Use this Codex home (default: CODEX_HOME or ~/.codex)")
    args = parser.parse_args(raw)
    if subcommand in {"switch", "use"} and args.target is None:
        parser.error(f"{subcommand} requires an account name or number")
    if args.device_auth and not args.login:
        parser.error("--device-auth requires --login")
    if (args.force or args.restart) and not (args.target or args.next):
        parser.error("--force and --restart require an account switch")
    manager = CodexProfileManager(args.force, args.restart, args.home)
    if args.list:
        manager.render_dashboard()
    elif args.current:
        print(manager.get_active() or "No saved account matches the current login")
    elif args.save:
        manager.save_current(args.save)
    elif args.login:
        manager.login_account(args.login, args.device_auth)
        offer_switch(manager, args.login)
    elif args.import_file:
        manager.import_auth(Path(args.import_file[1]), args.import_file[0])
        offer_switch(manager, args.import_file[0])
    elif args.backup:
        manager.backup(Path(args.backup))
    elif args.restore:
        manager.restore(Path(args.restore))
    elif args.rename:
        manager.rename_profile(*args.rename)
    elif args.delete:
        manager.delete_profile(args.delete)
    elif args.move:
        manager.reorder_profile(*args.move)
    elif args.check is not None:
        target = args.check or manager.get_active()
        if not target:
            raise SwitchError("No saved active account to inspect")
        manager.check_quota(target)
    elif args.next:
        return 0 if manager.cycle_next() else 1
    elif args.target:
        return 0 if manager.switch(args.target) else 1
    else:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            parser.error("Interactive mode requires a terminal; use --help for commands")
        interactive_tui(manager)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SwitchError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        sys.exit(130)
