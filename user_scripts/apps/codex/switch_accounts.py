#!/usr/bin/env python3
"""Codex Profile Manager & Account Switcher.

Manages multiple OpenAI Codex CLI accounts stored in CODEX_HOME/account-switcher/profiles.
Features:
- Safe switching preserving single-use refresh token rotation (referencing codex-switcher)
- Accurate process detection distinguishing interactive sessions from background helpers
- Live rate-limit quota & usage inspection via ChatGPT wham/usage backend
- Rich TUI and fast non-interactive CLI switching
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
import tomllib
import urllib.error
import urllib.parse
import urllib.request

# ==============================================================================
# 1. AUTONOMOUS FAIL-SAFE DEPENDENCY RESOLVER
# ==============================================================================
def resolve_dependencies() -> None:
    """Iterative dependency resolver with TTY awareness and PIP/AUR fallbacks."""
    requirements = {
        "rich": {"pac": "python-rich", "pip": "rich"},
        "questionary": {"pac": "python-questionary", "pip": "questionary"},
        "psutil": {"pac": "python-psutil", "pip": "psutil"},
    }

    missing = [mod for mod in requirements if importlib.util.find_spec(mod) is None]
    if not missing:
        return

    if not sys.stdout.isatty():
        print(f"\n[✗] FATAL: Missing dependencies ({', '.join(missing)}) in non-interactive shell.")
        print("[✗] Cannot invoke pacman/sudo. Please run interactively to bootstrap.")
        sys.exit(1)

    print(f"\n[*] Missing dependencies detected: {', '.join(missing)}")
    print("[*] Engaging autonomous fail-safe resolver...\n")

    subprocess.run(["sudo", "-v"], check=False)
    aur_helper = next((h for h in ["paru", "yay"] if shutil.which(h)), None)

    for mod in missing:
        pkg_pac = requirements[mod]["pac"]
        pkg_pip = requirements[mod]["pip"]
        print(f" -> Resolving '{mod}'...")

        success = False

        if aur_helper:
            res = subprocess.run(
                [aur_helper, "-S", "--needed", "--noconfirm", pkg_pac],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            success = res.returncode == 0

        if not success:
            res = subprocess.run(
                ["sudo", "pacman", "-S", "--needed", "--noconfirm", pkg_pac],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            success = res.returncode == 0

        if not success:
            print(f"    [!] '{pkg_pac}' absent from repos. Injecting via pip bypass...")
            res = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--user", "--break-system-packages", pkg_pip],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            success = res.returncode == 0

        if not success:
            print(f"\n[✗] FATAL: Absolute failure resolving '{mod}'.")
            sys.exit(1)

    print("\n[✓] Dependencies successfully satisfied. Starting manager...\n")
    os.execv(sys.executable, [sys.executable] + sys.argv)


resolve_dependencies()

from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
import psutil
import questionary

# ==============================================================================
# 2. UI THEMING & CONSTANTS
# ==============================================================================
custom_theme = Theme({
    "info": "dim cyan",
    "warning": "bold yellow",
    "error": "bold red",
    "success": "bold green",
    "highlight": "bold magenta",
    "muted": "dim white",
})

console = Console(theme=custom_theme)

custom_qstyle = questionary.Style([
    ("qmark", "fg:#c678dd bold"),
    ("question", "bold"),
    ("answer", "fg:#61afef bold"),
    ("pointer", "fg:#c678dd bold"),
    ("highlighted", "fg:#c678dd bold"),
    ("selected", "fg:#98c379 bold"),
    ("disabled", "fg:#5c6370 italic"),
])

NAME_REGEX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
CANCEL_VALUE = "← Cancel / Go Back"
DONE_VALUE = "✓ Done"

# OpenAI OAuth constants (referencing codex-switcher-main)
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
CHATGPT_BACKEND_API = "https://chatgpt.com/backend-api"
CHATGPT_ORIGIN = "https://chatgpt.com"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


class SwitchError(Exception):
    pass


# ==============================================================================
# 3. UTILITY & SECURITY HELPERS
# ==============================================================================
def home() -> Path:
    value = os.environ.get("CODEX_HOME")
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise SwitchError("CODEX_HOME must be an absolute path")
        return path
    return Path.home() / ".codex"


def check_name(name: str) -> str:
    name = name.strip()
    if not NAME_REGEX.fullmatch(name) or name in {".", ".."}:
        raise SwitchError(
            "Account name must be 1–64 letters, digits, dots, dashes, or underscores, starting with a letter or digit"
        )
    return name


def private_dir(path: Path) -> None:
    if path.is_symlink():
        raise SwitchError(f"Refusing symbolic link: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise SwitchError(f"Not a directory: {path}")
    os.chmod(path, 0o700)


def read_regular(path: Path) -> bytes | None:
    if path.is_symlink():
        raise SwitchError(f"Refusing symbolic link: {path}")
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise SwitchError(f"Not a regular file: {path}")
    return path.read_bytes()


def atomic_write(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise SwitchError(f"Refusing symbolic link: {path}")
    parent = path.parent
    private_dir(parent)
    fd, temp = tempfile.mkstemp(prefix=".switch-", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
        dir_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def parse_auth(data: bytes | None) -> dict:
    if not data:
        raise SwitchError("No Codex credentials found")
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwitchError("Invalid JSON in credentials file") from exc
    if not isinstance(value, dict):
        raise SwitchError("Invalid credentials format")
    tokens = value.get("tokens")
    if isinstance(tokens, dict) and all(
        isinstance(tokens.get(k), str) and tokens[k]
        for k in ("access_token", "refresh_token")
    ):
        return value
    if isinstance(value.get("OPENAI_API_KEY"), str) and value["OPENAI_API_KEY"]:
        return value
    raise SwitchError("Credentials contain neither usable ChatGPT tokens nor an API key")


def parse_jwt_claims(token: str | None) -> dict:
    if not token or not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = parts[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        data = json.loads(decoded)
        auth = data.get("https://api.openai.com/auth", {}) if isinstance(data, dict) else {}
        return {
            "email": data.get("email") if isinstance(data, dict) else None,
            "plan_type": auth.get("chatgpt_plan_type") if isinstance(auth, dict) else None,
            "account_id": auth.get("chatgpt_account_id") if isinstance(auth, dict) else None,
            "subscription_expires_at": auth.get("chatgpt_subscription_active_until") if isinstance(auth, dict) else None,
            "exp": data.get("exp") if isinstance(data, dict) else None,
        }
    except Exception:
        return {}


def identity(auth: dict) -> str:
    tokens = auth.get("tokens")
    if isinstance(tokens, dict) and tokens.get("account_id"):
        return "chatgpt:" + str(tokens["account_id"])
    if auth.get("OPENAI_API_KEY"):
        return "api:" + hashlib.sha256(auth["OPENAI_API_KEY"].encode()).hexdigest()
    if isinstance(tokens, dict) and tokens.get("id_token"):
        claims = parse_jwt_claims(tokens["id_token"])
        if claims.get("account_id"):
            return "chatgpt:" + str(claims["account_id"])
        if claims.get("email"):
            return "email:" + str(claims["email"])
        return "id:" + hashlib.sha256(tokens["id_token"].encode()).hexdigest()
    raise SwitchError("Cannot determine unique identity of credentials")


def ensure_file_store(codex_home: Path) -> None:
    """Ensure Codex CLI uses file-based credential storage."""
    config = codex_home / "config.toml"
    data = read_regular(config)
    if data is None:
        atomic_write(config, b'cli_auth_credentials_store = "file"\n')
        return
    try:
        parsed = tomllib.loads(data.decode("utf-8", errors="replace"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SwitchError("Cannot parse Codex config.toml") from exc
    store = parsed.get("cli_auth_credentials_store")
    if store == "file":
        return
    if store is not None:
        raise SwitchError(
            f'config.toml sets cli_auth_credentials_store = "{store}"; change it to "file" first'
        )
    atomic_write(config, b'cli_auth_credentials_store = "file"\n' + data)


def format_remaining_seconds(seconds: int | float | None) -> str:
    if seconds is None:
        return "N/A"
    if seconds <= 0:
        return "now"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours >= 24:
        days = hours // 24
        hours = hours % 24
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_timestamp(ts: int | float | None) -> str:
    if not ts:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
        return dt.strftime("%b %d, %H:%M")
    except Exception:
        return "N/A"


# ==============================================================================
# 4. PROCESS DETECTION & COLLISION HANDLING (CODEX-SWITCHER SPEC)
# ==============================================================================
def classify_codex_processes() -> tuple[list[psutil.Process], list[psutil.Process]]:
    """Accurately classify Codex processes on Linux.

    Referencing codex-switcher:
    - Interactive CLI sessions (with an attached TTY) and desktop app windows
      are considered active sessions that could conflict with an in-progress switch.
    - Background helpers such as 'codex app-server', IDE extension daemons
      (.antigravity, openai.chatgpt, .vscode), and headless loops do NOT block switching.
    """
    active_blocking: list[psutil.Process] = []
    background_helpers: list[psutil.Process] = []

    current_pid = os.getpid()
    parent_pid = os.getppid()
    exclude_pids = {current_pid, parent_pid}

    try:
        grandparent_pid = psutil.Process(parent_pid).ppid()
        exclude_pids.add(grandparent_pid)
    except Exception:
        pass

    for proc in psutil.process_iter(["pid", "name", "cmdline", "terminal", "uids"]):
        try:
            pid = proc.info["pid"]
            if pid in exclude_pids:
                continue

            uids = proc.info.get("uids")
            if uids and uids.real != os.getuid():
                continue

            name = (proc.info.get("name") or "").lower()
            cmdline_list = proc.info.get("cmdline") or []
            cmdline = " ".join(cmdline_list).lower()
            if not cmdline:
                continue

            if "switch_accounts" in cmdline:
                continue

            first_token = cmdline_list[0].lower() if cmdline_list else ""
            is_codex = (
                name in {"codex", "codex-cli", "chatgpt"}
                or first_token == "codex"
                or first_token.endswith("/codex")
                or "codex" in first_token
                or "codex app-server" in cmdline
            )
            if not is_codex:
                continue

            is_app_server = "codex app-server" in cmdline or "app-server daemon" in cmdline
            is_ide_plugin = any(
                token in cmdline
                for token in [".antigravity", ".vscode", "openai.chatgpt", "app-server-daemon"]
            )
            has_tty = bool(proc.info.get("terminal"))

            if is_app_server or is_ide_plugin:
                background_helpers.append(proc)
            elif has_tty:
                active_blocking.append(proc)
            else:
                background_helpers.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return active_blocking, background_helpers


def kill_processes(processes: list[psutil.Process], label: str = "conflicting") -> bool:
    """Safely terminate blocking processes with SIGTERM then SIGKILL."""
    if not processes:
        return True
    for proc in processes:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    gone, alive = psutil.wait_procs(processes, timeout=3.0)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    console.print(f"[success]✓ Closed {len(processes)} {label} process(es).[/success]")
    return True


def restart_background_services() -> None:
    """Terminate background codex app-server/extension helpers so they pick up new auth.json."""
    _, background = classify_codex_processes()
    if not background:
        return
    kill_processes(background, label="background Codex helper")


# ==============================================================================
# 5. TOKEN REFRESH & LIVE QUOTA APIS (CODEX-SWITCHER SPEC)
# ==============================================================================
def is_access_token_expired(access_token: str | None) -> bool:
    """Check if the access token JWT has expired or will expire within 60s."""
    if not access_token:
        return True
    claims = parse_jwt_claims(access_token)
    exp = claims.get("exp")
    if exp:
        return time.time() >= (exp - 60)
    return False


def refresh_chatgpt_tokens(refresh_token: str) -> dict | None:
    """Exchange refresh token via auth.openai.com (referencing codex-switcher token_refresh.rs)."""
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": OPENAI_CLIENT_ID,
    }).encode("utf-8")

    req = urllib.request.Request(
        OPENAI_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        console.print(f"[warning]! Token refresh network call failed: {e}[/warning]")
        return None


def fetch_account_usage(auth_dict: dict) -> dict | None:
    """Fetch live 5-hour and weekly usage metrics from ChatGPT wham/usage backend."""
    tokens = auth_dict.get("tokens")
    if not isinstance(tokens, dict):
        return None

    access_token = tokens.get("access_token")
    if not access_token:
        return None

    account_id = tokens.get("account_id")
    if not account_id and tokens.get("id_token"):
        account_id = parse_jwt_claims(tokens["id_token"]).get("account_id")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": CHATGPT_ORIGIN,
        "Referer": CHATGPT_ORIGIN,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    if account_id:
        headers["chatgpt-account-id"] = str(account_id)

    url = f"{CHATGPT_BACKEND_API}/wham/usage"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # Token expired - try refreshing once
            refresh_tok = tokens.get("refresh_token")
            if refresh_tok:
                refreshed = refresh_chatgpt_tokens(refresh_tok)
                if refreshed and "access_token" in refreshed:
                    headers["Authorization"] = f"Bearer {refreshed['access_token']}"
                    req2 = urllib.request.Request(url, headers=headers)
                    try:
                        with urllib.request.urlopen(req2, timeout=12) as resp2:
                            return json.loads(resp2.read().decode("utf-8"))
                    except Exception:
                        pass
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


# ==============================================================================
# 6. PROFILE MANAGER CORE
# ==============================================================================
class CodexProfileManager:
    def __init__(self, force_mode: bool = False, restart_mode: bool = False) -> None:
        self.force_mode = force_mode
        self.restart_mode = restart_mode
        self.codex_home = home()
        self.root = self.codex_home / "account-switcher"
        self.profiles_dir = self.root / "profiles"
        self.active_file = self.root / "active"
        self.order_file = self.root / "order.txt"
        self.lock_file = self.root / ".lock"

        private_dir(self.codex_home)
        private_dir(self.root)
        private_dir(self.profiles_dir)

    @contextlib.contextmanager
    def locked(self):
        with open(self.lock_file, "a+b") as lock_f:
            os.fchmod(lock_f.fileno(), 0o600)
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)

    def get_active(self) -> str | None:
        data = read_regular(self.active_file)
        if not data:
            return None
        val = data.decode("utf-8", errors="replace").strip()
        return val if (val and (self.profiles_dir / f"{val}.json").is_file()) else None

    def set_active(self, name: str) -> None:
        atomic_write(self.active_file, (check_name(name) + "\n").encode("utf-8"))

    def _read_order(self) -> list[str]:
        data = read_regular(self.order_file)
        if not data:
            return []
        lines = data.decode("utf-8", errors="replace").splitlines()
        return [l.strip() for l in lines if l.strip()]

    def _persist_order(self, order: list[str]) -> None:
        atomic_write(self.order_file, ("\n".join(order) + "\n").encode("utf-8"))

    def get_all(self) -> list[str]:
        existing = sorted([
            p.stem
            for p in self.profiles_dir.glob("*.json")
            if p.is_file() and not p.is_symlink()
        ])
        ordered = [name for name in self._read_order() if name in existing]
        remaining = [name for name in existing if name not in ordered]
        return ordered + remaining

    def get_profile_path(self, name: str) -> Path:
        return self.profiles_dir / f"{check_name(name)}.json"

    def read_profile_auth(self, name: str) -> dict | None:
        data = read_regular(self.get_profile_path(name))
        if not data:
            return None
        try:
            return parse_auth(data)
        except Exception:
            return None

    def get_profile_metadata(self, name: str) -> dict:
        auth = self.read_profile_auth(name)
        if not auth:
            return {
                "name": name,
                "plan": "Unknown",
                "email": "None",
                "status": "Void",
                "auth_mode": "None",
            }

        tokens = auth.get("tokens")
        if isinstance(tokens, dict):
            id_claims = parse_jwt_claims(tokens.get("id_token"))
            acc_claims = parse_jwt_claims(tokens.get("access_token"))
            plan = id_claims.get("plan_type") or "ChatGPT"
            email = id_claims.get("email") or "Account"
            acc_exp = acc_claims.get("exp")
            if acc_exp and time.time() >= acc_exp:
                status = "Expired"
            else:
                status = "Secured"
            return {
                "name": name,
                "plan": plan.capitalize(),
                "email": email,
                "status": status,
                "auth_mode": "chatgpt",
            }
        elif auth.get("OPENAI_API_KEY"):
            return {
                "name": name,
                "plan": "API Key",
                "email": "OpenAI Platform",
                "status": "Secured",
                "auth_mode": "api_key",
            }
        return {
            "name": name,
            "plan": "Unknown",
            "email": "None",
            "status": "Void",
            "auth_mode": "unknown",
        }

    def sync_active_tokens(self) -> None:
        """Preserve any OAuth refresh tokens that Codex rotated during the session."""
        active = self.get_active()
        if not active:
            return
        live_auth_file = self.codex_home / "auth.json"
        live_data = read_regular(live_auth_file)
        if not live_data:
            return
        try:
            live_auth = parse_auth(live_data)
        except Exception:
            return

        stored_auth = self.read_profile_auth(active)
        if not stored_auth:
            return

        try:
            if identity(live_auth) == identity(stored_auth):
                atomic_write(self.get_profile_path(active), live_data)
        except Exception:
            pass

    def check_processes_before_switch(self) -> bool:
        """Handle active processes based on interactive / force / restart flags."""
        active_blocking, _ = classify_codex_processes()
        if not active_blocking:
            return True

        if self.restart_mode:
            console.print(
                f"[warning]! {len(active_blocking)} active Codex session(s) detected — closing for switch...[/warning]"
            )
            return kill_processes(active_blocking)

        if self.force_mode:
            console.print(
                "[warning]! Force override active: Bypassing active session collision checks.[/warning]"
            )
            return True

        if not sys.stdin.isatty():
            pids = ", ".join(str(p.pid) for p in active_blocking)
            console.print(
                f"\n[error]✗ Active Codex CLI session(s) detected (PIDs: {pids}). "
                f"Aborting switch to prevent session corruption. Use -f/--force or -r/--restart.[/error]"
            )
            return False

        console.print(
            f"\n[warning]! {len(active_blocking)} active interactive Codex session(s) detected![/warning]"
        )
        for p in active_blocking:
            try:
                console.print(f"  [muted]• PID {p.pid}: {' '.join(p.cmdline()[:3])}[/muted]")
            except Exception:
                pass

        action = questionary.select(
            "Resolve collision:",
            choices=[
                questionary.Choice("Kill & Proceed (Recommended)", value="kill"),
                questionary.Choice("Ignore & Proceed (Risky)", value="ignore"),
                questionary.Choice("Abort (Safe)", value="cancel"),
            ],
            default="kill",
            pointer="❯",
            style=custom_qstyle,
        ).ask()

        if action == "kill":
            return kill_processes(active_blocking)
        elif action == "ignore":
            console.print("[warning]Proceeding with collision risk...[/warning]")
            return True
        else:
            console.print("[error]Operation aborted.[/error]")
            return False

    def switch(self, name: str) -> bool:
        """Switch to a specific profile, preserving token rotation."""
        name = name.strip()
        # Support numeric indices (e.g. '1', '2')
        if name.isdigit():
            idx = int(name) - 1
            all_profiles = self.get_all()
            if 0 <= idx < len(all_profiles):
                name = all_profiles[idx]
            else:
                console.print(f"[error]✗ Profile index #{name} out of bounds (1–{len(all_profiles)}).[/error]")
                return False

        try:
            check_name(name)
        except SwitchError as e:
            console.print(f"[error]✗ {e}[/error]")
            return False

        target_file = self.get_profile_path(name)
        if not target_file.is_file():
            console.print(f"[error]✗ Profile '{name}' does not exist.[/error]")
            return False

        with self.locked():
            ensure_file_store(self.codex_home)
            current_active = self.get_active()
            if current_active == name:
                console.print(f"[info]› State unchanged. Already active on '{name}'.[/info]")
                if self.restart_mode:
                    restart_background_services()
                return True

            if not self.check_processes_before_switch():
                return False

            # 1. Sync live credentials from current active account to prevent burning rotated refresh token
            self.sync_active_tokens()

            # 2. Read target profile
            target_data = read_regular(target_file)
            target_auth = parse_auth(target_data)

            # 3. If target's access token is expired, attempt fresh OAuth exchange
            tokens = target_auth.get("tokens")
            if isinstance(tokens, dict) and is_access_token_expired(tokens.get("access_token")):
                refresh_token = tokens.get("refresh_token")
                if refresh_token:
                    console.print(f"[info]› Access token for '{name}' expired; refreshing via OAuth...[/info]")
                    refreshed = refresh_chatgpt_tokens(refresh_token)
                    if refreshed and "access_token" in refreshed:
                        tokens["access_token"] = refreshed["access_token"]
                        if refreshed.get("refresh_token"):
                            tokens["refresh_token"] = refreshed["refresh_token"]
                        if refreshed.get("id_token"):
                            tokens["id_token"] = refreshed["id_token"]
                        target_auth["tokens"] = tokens
                        target_data = json.dumps(target_auth, indent=2).encode("utf-8")
                        atomic_write(target_file, target_data)
                        console.print(f"[success]✓ Refreshed credentials for '{name}'.[/success]")

            # 4. Atomically write to CODEX_HOME / auth.json
            atomic_write(self.codex_home / "auth.json", target_data)
            self.set_active(name)

            # 5. Reload background helpers (e.g. codex app-server) so they read new credentials
            restart_background_services()

            console.print(f"\n[success]✓ Switched to profile: '{name}'.[/success]")
            return True

    def cycle_next(self) -> bool:
        profiles = self.get_all()
        if not profiles:
            console.print("[error]✗ No saved profiles to cycle.[/error]")
            return False
        active = self.get_active()
        next_profile = (
            profiles[0]
            if active not in profiles
            else profiles[(profiles.index(active) + 1) % len(profiles)]
        )
        console.print(f"\n[info]› Cycling to next profile ({next_profile})...[/info]")
        return self.switch(next_profile)

    def save_current(self, name: str) -> bool:
        name = name.strip()
        check_name(name)
        with self.locked():
            ensure_file_store(self.codex_home)
            data = read_regular(self.codex_home / "auth.json")
            if not data:
                console.print("[error]✗ No active auth.json found in Codex home.[/error]")
                return False
            parse_auth(data)
            target = self.get_profile_path(name)
            atomic_write(target, data)
            self.set_active(name)
            order = self.get_all()
            if name not in order:
                order.append(name)
                self._persist_order(order)
            console.print(f"[success]✓ Saved current login as '{name}'.[/success]")
            return True

    def login_account(self, name: str, device_auth: bool = False) -> bool:
        name = name.strip()
        check_name(name)
        target = self.get_profile_path(name)
        if target.is_file():
            console.print(f"[error]✗ Profile '{name}' already exists.[/error]")
            return False

        codex_bin = shutil.which("codex")
        if not codex_bin:
            console.print("[error]✗ 'codex' executable not found in PATH.[/error]")
            return False

        with self.locked():
            ensure_file_store(self.codex_home)
            self.sync_active_tokens()

            # Backup current auth.json
            current_auth = read_regular(self.codex_home / "auth.json")
            (self.codex_home / "auth.json").unlink(missing_ok=True)

            cmd = [codex_bin, "login", "-c", 'cli_auth_credentials_store="file"']
            if device_auth:
                cmd.append("--device-auth")

            console.print(f"[info]› Running Codex login for '{name}'...[/info]")
            try:
                res = subprocess.run(cmd, check=False)
                if res.returncode != 0:
                    console.print(f"[error]✗ Login exited with status {res.returncode}[/error]")
                    if current_auth:
                        atomic_write(self.codex_home / "auth.json", current_auth)
                    return False

                new_data = read_regular(self.codex_home / "auth.json")
                if not new_data:
                    console.print("[error]✗ No credentials produced by login.[/error]")
                    if current_auth:
                        atomic_write(self.codex_home / "auth.json", current_auth)
                    return False

                parse_auth(new_data)
                atomic_write(target, new_data)
                self.set_active(name)
                order = self.get_all()
                if name not in order:
                    order.append(name)
                    self._persist_order(order)
                console.print(f"[success]✓ Successfully signed in and created profile '{name}'.[/success]")
                return True
            except Exception as e:
                console.print(f"[error]✗ Login failure: {e}[/error]")
                if current_auth:
                    atomic_write(self.codex_home / "auth.json", current_auth)
                return False

    def import_auth(self, source_path: Path, name: str, switch_now: bool | None = None) -> bool:
        name = name.strip()
        check_name(name)
        target = self.get_profile_path(name)
        if target.is_file():
            console.print(f"[error]✗ Profile '{name}' already exists.[/error]")
            return False

        data = read_regular(source_path)
        if not data:
            console.print(f"[error]✗ Cannot read file: {source_path}[/error]")
            return False

        try:
            parse_auth(data)
        except Exception as e:
            console.print(f"[error]✗ Invalid credentials in source: {e}[/error]")
            return False

        with self.locked():
            atomic_write(target, data)
            order = self.get_all()
            if name not in order:
                order.append(name)
                self._persist_order(order)
            console.print(f"[success]✓ Imported profile '{name}' from {source_path}.[/success]")

            should_switch = switch_now
            if should_switch is None and sys.stdin.isatty():
                should_switch = questionary.confirm(
                    f"Switch to newly imported profile '{name}' now?",
                    default=True,
                    style=custom_qstyle,
                ).ask()
            if should_switch:
                self.switch(name)
            return True

    def delete_profile(self, name: str, confirm: bool | None = None) -> bool:
        name = name.strip()
        active = self.get_active()
        if name == active:
            console.print(f"[error]✗ Cannot delete active profile '{name}'. Switch to another first.[/error]")
            return False
        target = self.get_profile_path(name)
        if not target.is_file():
            console.print(f"[error]✗ Profile '{name}' does not exist.[/error]")
            return False

        should_del = confirm
        if should_del is None and sys.stdin.isatty():
            should_del = questionary.confirm(
                f"Permanently delete profile '{name}'?",
                default=False,
                style=custom_qstyle,
            ).ask()

        if should_del:
            with self.locked():
                target.unlink(missing_ok=True)
                order = [x for x in self._read_order() if x != name]
                self._persist_order(order)
                console.print(f"[success]✓ Profile '{name}' deleted.[/success]")
                return True
        return False

    def rename_profile(self, old_name: str, new_name: str) -> bool:
        old_name = old_name.strip()
        new_name = new_name.strip()
        if old_name == new_name:
            console.print("[info]› New name identical to current name.[/info]")
            return False
        check_name(old_name)
        check_name(new_name)

        old_file = self.get_profile_path(old_name)
        new_file = self.get_profile_path(new_name)
        if not old_file.is_file():
            console.print(f"[error]✗ Profile '{old_name}' does not exist.[/error]")
            return False
        if new_file.is_file():
            console.print(f"[error]✗ Profile '{new_name}' already exists.[/error]")
            return False

        with self.locked():
            old_file.rename(new_file)
            if self.get_active() == old_name:
                self.set_active(new_name)
            order = [new_name if x == old_name else x for x in self._read_order()]
            self._persist_order(order)
            console.print(f"[success]✓ Profile '{old_name}' renamed to '{new_name}'.[/success]")
            return True

    def reorder_profile(self, name: str, direction: str) -> bool:
        profiles = self.get_all()
        if name not in profiles:
            console.print(f"[error]✗ Profile '{name}' does not exist.[/error]")
            return False
        if len(profiles) < 2:
            console.print("[info]› Need at least two profiles to reorder.[/info]")
            return False

        idx = profiles.index(name)
        if direction == "up":
            if idx == 0:
                console.print(f"[info]› '{name}' is already at the top.[/info]")
                return False
            profiles[idx], profiles[idx - 1] = profiles[idx - 1], profiles[idx]
        elif direction == "down":
            if idx == len(profiles) - 1:
                console.print(f"[info]› '{name}' is already at the bottom.[/info]")
                return False
            profiles[idx], profiles[idx + 1] = profiles[idx + 1], profiles[idx]
        else:
            console.print(f"[error]✗ Invalid direction: '{direction}'.[/error]")
            return False

        self._persist_order(profiles)
        console.print(f"[success]✓ Moved '{name}' {direction} (position {profiles.index(name) + 1}).[/success]")
        return True

    def check_quota(self, name: str) -> bool:
        """Fetch live rate-limit quota from ChatGPT and display a clean table."""
        name = name.strip()
        auth = self.read_profile_auth(name)
        if not auth:
            console.print(f"[error]✗ Profile '{name}' credentials not found.[/error]")
            return False

        console.print(f"\n[info]› Querying live Codex usage for '{name}'...[/info]")
        res = fetch_account_usage(auth)
        if not res:
            console.print(f"[error]✗ Could not retrieve usage for '{name}'.[/error]")
            return False

        if "error" in res:
            console.print(f"[warning]! Quota API returned: {res['error']}[/warning]")
            return False

        email = res.get("email") or "Unknown"
        plan = (res.get("plan_type") or "Plus").capitalize()
        rate_limit = res.get("rate_limit") or {}
        allowed = rate_limit.get("allowed", True)
        limit_reached = rate_limit.get("limit_reached", False)

        status_text = (
            Text("● OK", style="bold green")
            if allowed and not limit_reached
            else Text("● LIMIT REACHED", style="bold red")
        )

        table = Table(
            title=f"Live Quota: {name}",
            title_style="bold magenta",
            border_style="magenta",
            header_style="bold cyan",
            box=box.ROUNDED,
            padding=(0, 2),
            collapse_padding=True,
        )
        table.add_column("Window", style="bold cyan")
        table.add_column("Used %", justify="right")
        table.add_column("Reset In", justify="right")
        table.add_column("Reset At", justify="left")

        # 1. Primary window (5h)
        pw = rate_limit.get("primary_window") or {}
        if pw:
            used = pw.get("used_percent", 0)
            u_style = "bold red" if used >= 80 else ("bold yellow" if used >= 50 else "bold green")
            table.add_row(
                "5-Hour Window",
                Text(f"{used}%", style=u_style),
                format_remaining_seconds(pw.get("reset_after_seconds")),
                format_timestamp(pw.get("reset_at")),
            )

        # 2. Secondary window (weekly)
        sw = rate_limit.get("secondary_window") or {}
        if sw:
            used = sw.get("used_percent", 0)
            u_style = "bold red" if used >= 80 else ("bold yellow" if used >= 50 else "bold green")
            table.add_row(
                "Weekly Window",
                Text(f"{used}%", style=u_style),
                format_remaining_seconds(sw.get("reset_after_seconds")),
                format_timestamp(sw.get("reset_at")),
            )

        # Credits
        credits_info = res.get("credits") or {}
        balance = credits_info.get("balance", "0")
        table.add_row("Credits Balance", f"${balance}", "-", "-")

        console.print("")
        meta_line = Text.assemble(
            ("Email: ", "dim cyan"), (f"{email}  •  ", "bold white"),
            ("Plan: ", "dim cyan"), (f"{plan}  •  ", "bold white"),
            ("Status: ", "dim cyan"), status_text
        )
        console.print(Align.center(meta_line))
        console.print("")
        console.print(Align.center(table))
        console.print("")
        return True

    def render_dashboard(self) -> None:
        active = self.get_active()
        profiles = self.get_all()

        table = Table(
            title="Local Codex Account Matrix",
            title_style="bold magenta",
            border_style="magenta",
            header_style="bold cyan",
            box=box.ROUNDED,
            padding=(0, 2),
            collapse_padding=True,
            show_lines=False,
        )
        table.add_column("#", justify="right", style="dim cyan", no_wrap=True)
        table.add_column("State", justify="left", no_wrap=True)
        table.add_column("Account Name", style="bold white", no_wrap=True)
        table.add_column("Plan", style="dim white", no_wrap=True)
        table.add_column("Email / ID", style="dim white", no_wrap=True)
        table.add_column("Status", justify="center", no_wrap=True)

        for idx, p in enumerate(profiles, start=1):
            is_active = p == active
            state_text = (
                Text("● ACTIVE", style="bold green")
                if is_active
                else Text("○ STANDBY", style="dim white")
            )

            meta = self.get_profile_metadata(p)
            status_style = "bold cyan" if meta["status"] == "Secured" else ("bold yellow" if meta["status"] == "Expired" else "dim yellow")
            status_text = Text(meta["status"], style=status_style)

            table.add_row(
                str(idx),
                state_text,
                p,
                meta["plan"],
                meta["email"],
                status_text,
            )

        if not profiles:
            console.print(Align.center("[muted]No accounts found. Use 'Save current' or 'Login' to begin.[/muted]"))
        else:
            console.print(Align.center(table))


# ==============================================================================
# 7. ROUTER & EVENT LOOP
# ==============================================================================
def render_screen(manager: CodexProfileManager) -> None:
    console.clear()
    title = Text("◆ Codex Profile Manager", style="bold magenta")
    subtitle = Text("Account Isolation & Credentials Switcher", style="dim cyan")
    header = Panel(
        Align.center(Text.assemble(title, "\n", subtitle)),
        border_style="magenta",
        box=box.ROUNDED,
        expand=False,
        padding=(0, 3),
    )
    console.print("")
    console.print(Align.center(header))
    console.print("")
    manager.render_dashboard()
    console.print("")


def build_profile_choices(
    profiles: list[str],
    active_profile: str | None = None,
    lock_active: bool = False,
) -> list[questionary.Choice]:
    choices = []
    for p in profiles:
        is_active = p == active_profile
        if lock_active and is_active:
            choices.append(questionary.Choice(f"{p} (Active - Locked)", value=p, disabled="Cannot delete active account"))
        elif is_active:
            choices.append(questionary.Choice(f"{p} (Active)", value=p))
        else:
            choices.append(questionary.Choice(p, value=p))
    choices.append(questionary.Choice(CANCEL_VALUE, value=CANCEL_VALUE))
    return choices


def interactive_tui(manager: CodexProfileManager) -> None:
    while True:
        render_screen(manager)

        profiles = manager.get_all()
        active = manager.get_active()
        main_choices = []

        if profiles:
            main_choices.append(questionary.Choice("Switch Account", value="switch"))
            main_choices.append(questionary.Choice("Cycle to Next Account", value="cycle"))
            main_choices.append(questionary.Choice("Check Account Quota / Rate Limits", value="check"))

        main_choices.extend([
            questionary.Choice("Restart Background Services (Reload Auth)", value="restart_services"),
            questionary.Choice("Save / Backup Current Session", value="save"),
            questionary.Choice("Login New Account (OAuth / Device)", value="login"),
            questionary.Choice("Import auth.json", value="import"),
            questionary.Choice(
                "Delete Account",
                value="delete",
                disabled="No accounts saved" if not profiles else ("Cannot delete the only active account" if len(profiles) == 1 and active in profiles else None),
            ),
            questionary.Choice(
                "Rename Account",
                value="rename",
                disabled="No accounts saved" if not profiles else None,
            ),
            questionary.Choice(
                "Reorder Accounts",
                value="reorder",
                disabled="Need at least two accounts" if len(profiles) < 2 else None,
            ),
            questionary.Choice("Quit", value="quit"),
        ])

        try:
            action = questionary.select(
                "Select Action:",
                choices=main_choices,
                pointer="❯",
                style=custom_qstyle,
            ).ask()
        except KeyboardInterrupt:
            console.print("\n[info]Session terminated via interrupt.[/info]")
            break

        if action is None or action == "quit":
            console.print("[info]Session terminated.[/info]")
            break

        console.print("")

        try:
            active = manager.get_active()
            match action:
                case "switch":
                    target = questionary.select(
                        "Select account to switch to:",
                        choices=build_profile_choices(profiles, active_profile=active),
                        default=active if active in profiles else None,
                        pointer="❯",
                        style=custom_qstyle,
                    ).ask()

                    if target and target != CANCEL_VALUE:
                        if manager.switch(target):
                            break
                        questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()

                case "cycle":
                    if manager.cycle_next():
                        break
                    questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()

                case "check":
                    target = questionary.select(
                        "Select account to inspect quota:",
                        choices=build_profile_choices(profiles, active_profile=active),
                        default=active if active in profiles else None,
                        pointer="❯",
                        style=custom_qstyle,
                    ).ask()

                    if target and target != CANCEL_VALUE:
                        manager.check_quota(target)
                        questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()

                case "restart_services":
                    restart_background_services()
                    questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()

                case "save":
                    name = questionary.text(
                        "Account name to save current session as:",
                        default=active or "",
                        style=custom_qstyle,
                    ).ask()
                    if name and name.strip():
                        manager.save_current(name.strip())
                        questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()

                case "login":
                    name = questionary.text("Enter name for the new account:", style=custom_qstyle).ask()
                    if name and name.strip():
                        mode = questionary.select(
                            "Login method:",
                            choices=[
                                questionary.Choice("Browser OAuth (Default)", value=False),
                                questionary.Choice("Device Auth Code (--device-auth)", value=True),
                                questionary.Choice(CANCEL_VALUE, value="cancel"),
                            ],
                            style=custom_qstyle,
                        ).ask()
                        if mode != "cancel" and mode is not None:
                            manager.login_account(name.strip(), device_auth=bool(mode))
                            questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()

                case "import":
                    path_str = questionary.text("Path to auth.json file:", style=custom_qstyle).ask()
                    if path_str and path_str.strip():
                        src = Path(path_str.strip()).expanduser()
                        name = questionary.text(
                            "Account name for this import:",
                            default=src.stem if src.stem != "auth" else "",
                            style=custom_qstyle,
                        ).ask()
                        if name and name.strip():
                            manager.import_auth(src, name.strip())
                            questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()

                case "delete":
                    target = questionary.select(
                        "Select account to delete:",
                        choices=build_profile_choices(profiles, active_profile=active, lock_active=True),
                        pointer="❯",
                        style=custom_qstyle,
                    ).ask()
                    if target and target != CANCEL_VALUE:
                        manager.delete_profile(target)
                        questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()

                case "rename":
                    target = questionary.select(
                        "Select account to rename:",
                        choices=build_profile_choices(profiles, active_profile=active),
                        pointer="❯",
                        style=custom_qstyle,
                    ).ask()
                    if target and target != CANCEL_VALUE:
                        new_name = questionary.text("Enter new account name:", style=custom_qstyle).ask()
                        if new_name and new_name.strip():
                            manager.rename_profile(target, new_name.strip())
                            questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()

                case "reorder":
                    target = questionary.select(
                        "Select account to move:",
                        choices=build_profile_choices(profiles, active_profile=active),
                        pointer="❯",
                        style=custom_qstyle,
                    ).ask()
                    if target and target != CANCEL_VALUE:
                        while True:
                            move_action = questionary.select(
                                f"Move '{target}' where?",
                                choices=[
                                    questionary.Choice("↑ Move Up", value="up"),
                                    questionary.Choice("↓ Move Down", value="down"),
                                    questionary.Choice(DONE_VALUE, value=DONE_VALUE),
                                ],
                                pointer="❯",
                                style=custom_qstyle,
                            ).ask()
                            if move_action is None or move_action == DONE_VALUE:
                                break
                            if manager.reorder_profile(target, move_action):
                                render_screen(manager)

        except KeyboardInterrupt:
            continue


# ==============================================================================
# 8. CLI ENTRYPOINT
# ==============================================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Codex Profile Manager & Account Switcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)

    # Normalize legacy subcommands for backwards compatibility
    if raw_args:
        first = raw_args[0]
        if first in ("switch", "use") and len(raw_args) > 1:
            raw_args = [raw_args[1]] + raw_args[2:]
        elif first == "list":
            raw_args = ["-l"] + raw_args[1:]
        elif first == "current":
            raw_args = ["--current"] + raw_args[1:]
        elif first == "save" and len(raw_args) > 1:
            raw_args = ["--save", raw_args[1]] + raw_args[2:]
        elif first == "login" and len(raw_args) > 1:
            raw_args = ["--login", raw_args[1]] + raw_args[2:]
        elif first == "import" and len(raw_args) > 2:
            raw_args = ["--import-file", raw_args[1], raw_args[2]] + raw_args[3:]
        elif first == "check":
            target_arg = raw_args[1] if len(raw_args) > 1 and not raw_args[1].startswith("-") else "__active__"
            rest = raw_args[2:] if target_arg != "__active__" else raw_args[1:]
            raw_args = ["-c", target_arg] + rest

    parser = argparse.ArgumentParser(
        description="Codex Profile Manager & Account Switcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Direct profile positional argument (e.g. `./switch_accounts.py ubaid` or `./switch_accounts.py 2`)
    parser.add_argument("target", nargs="?", help="Direct account name or number to switch to")

    parser.add_argument("-l", "--list", action="store_true", help="List all accounts in a table and exit")
    parser.add_argument("--current", action="store_true", help="Print active account name and exit")
    parser.add_argument("-n", "--next", action="store_true", help="Cycle to next account and exit")
    parser.add_argument("-f", "--force", action="store_true", help="Bypass process check and force switch")
    parser.add_argument("-r", "--restart", action="store_true", help="Close active Codex sessions, switch, and reload services")
    parser.add_argument(
        "-c", "--check",
        nargs="?",
        const="__active__",
        metavar="ACCOUNT",
        help="Query live usage/quota for account (defaults to active)",
    )
    parser.add_argument("--save", metavar="NAME", help="Save current Codex login as a profile")
    parser.add_argument("--import-file", nargs=2, metavar=("NAME", "PATH"), help="Import credentials from auth.json")
    parser.add_argument("--login", metavar="NAME", help="Run 'codex login' and save as profile")
    parser.add_argument("--device-auth", action="store_true", help="Use device code authorization when logging in")

    args = parser.parse_args(raw_args)

    manager = CodexProfileManager(force_mode=args.force, restart_mode=args.restart)

    if args.list:
        manager.render_dashboard()
        return 0
    if args.current:
        print(manager.get_active() or "No active account saved")
        return 0
    if args.save:
        return 0 if manager.save_current(args.save) else 1
    if args.login:
        return 0 if manager.login_account(args.login, device_auth=args.device_auth) else 1
    if args.import_file:
        name, path_str = args.import_file
        return 0 if manager.import_auth(Path(path_str).expanduser(), name) else 1
    if args.check is not None:
        target = manager.get_active() if args.check == "__active__" else args.check
        if not target:
            console.print("[error]✗ No active account to check.[/error]")
            return 1
        return 0 if manager.check_quota(target) else 1
    if args.next:
        return 0 if manager.cycle_next() else 1
    if args.target:
        return 0 if manager.switch(args.target) else 1

    # Interactive TUI
    if not sys.stdin.isatty():
        console.print(
            "[error]✗ Interactive mode requires a terminal. "
            "Pass an account name, number, -l, -n, -c, -f, or -r instead.[/error]"
        )
        return 1

    interactive_tui(manager)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[error]Process killed via SIGINT.[/error]")
        sys.exit(130)
