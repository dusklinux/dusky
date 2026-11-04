#!/usr/bin/env python3

import sys
import subprocess
import shutil
import importlib.util
import os
import re
import argparse
import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import platform
import time
from pathlib import Path

# ==========================================
# 1. AUTONOMOUS FAIL-SAFE DEPENDENCY RESOLVER
# ==========================================
# ==========================================
# 1. AUTONOMOUS FAIL-SAFE DEPENDENCY RESOLVER
# ==========================================
# The only reliable signal is "can THIS interpreter import it". On a bleeding-edge
# interpreter (3.15 here, while the repos are still built for 3.14) pacman installs
# python-questionary into /usr/lib/python3.14/site-packages, exits 0, and the module
# stays invisible - which turned the old resolver into an endless sudo/re-exec loop.
# Never trust an installer's exit code, and never re-exec blindly.
DEPS: dict[str, tuple[str, str]] = {
    "rich": ("python-rich", "rich"),
    "keyring": ("python-keyring", "keyring"),
    "questionary": ("python-questionary", "questionary"),
    "psutil": ("python-psutil", "psutil"),
}


def _importable(mod: str) -> bool:
    """True when `mod` resolves for the running interpreter."""
    importlib.invalidate_caches()
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _missing() -> list[str]:
    return [mod for mod in DEPS if not _importable(mod)]


def _run(cmd: list[str], timeout: int = 900) -> tuple[bool, str]:
    """Run an installer quietly, keeping the tail of its output for diagnostics."""
    try:
        res = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (res.stderr or res.stdout or "").strip()
    return res.returncode == 0, "\n".join(output.splitlines()[-4:])


def _distro_python_matches() -> bool:
    """True when distro python packages are usable by the running interpreter.

    pacman builds python-* against the distro's default interpreter; when a newer
    one is running they can never be imported, so pacman must be skipped entirely.
    """
    distro = shutil.which("python3", path="/usr/bin:/bin")
    if not distro or Path(distro).resolve() == Path(sys.executable).resolve():
        return True
    ok, out = _run([distro, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], timeout=20)
    current = "%d.%d" % sys.version_info[:2]
    return ok and out.strip().splitlines()[-1:] == [current]


def _privileges_available() -> bool:
    """True when sudo is usable now (so non-interactive runs never hang on a prompt)."""
    if shutil.which("sudo") is None:
        return False
    if sys.stdin.isatty():
        return subprocess.run(["sudo", "-v"], check=False).returncode == 0
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


def resolve_dependencies() -> None:
    """Satisfy missing deps for the running interpreter, in one bounded pass."""
    missing = _missing()
    if not missing:
        return

    print(f"\n[*] Missing dependencies detected: {', '.join(missing)}")
    print("[*] Engaging autonomous fail-safe resolver...")

    pacman_usable = _distro_python_matches()
    aur_helper = next((h for h in ("paru", "yay") if shutil.which(h)), None)
    can_install_pkgs = pacman_usable and _privileges_available()

    if pacman_usable:
        print("")
    else:
        print(f"[!] Repo python packages are built for the distro interpreter, not "
              f"{sys.version.split()[0]}; resolving for {sys.executable} via pip.\n")

    try:
        import site as _site

        user_site = _site.getusersitepackages()
    except Exception:
        user_site = ""
    if user_site and user_site not in sys.path:
        sys.path.append(user_site)

    pip_flags = (
        ["--disable-pip-version-check"]
        if sys.prefix != sys.base_prefix
        else ["--user", "--break-system-packages", "--disable-pip-version-check"]
    )

    for mod in missing:
        pkg_pac, pkg_pip = DEPS[mod]
        print(f" -> Resolving '{mod}'...")
        ok, detail = False, ""

        if can_install_pkgs:
            if aur_helper:
                ok, detail = _run([aur_helper, "-S", "--needed", "--noconfirm", pkg_pac])
            if not ok:
                ok, detail = _run(["sudo", "pacman", "-S", "--needed", "--noconfirm", pkg_pac])
            # An installer exit code proves nothing: verify the actual import.
            ok = _importable(mod)
            if not ok:
                print(f"    [!] '{pkg_pac}' is installed but '{mod}' is still not importable here.")

        if not ok:
            _, detail = _run([sys.executable, "-m", "pip", "install", *pip_flags, pkg_pip])
            ok = _importable(mod)

        if not ok:
            print(f"\n[✗] FATAL: Could not make '{mod}' importable for {sys.executable}.")
            if detail:
                print(f"    Installer output: {detail}")
            print(f"    Manual fix: {sys.executable} -m pip install {' '.join(pip_flags)} {pkg_pip}")
            sys.exit(1)

    print("\n[✓] Dependencies successfully satisfied. Starting manager...\n")


resolve_dependencies()

from rich import box
from rich.console import Console
from rich.theme import Theme
from rich.panel import Panel
from rich.table import Table
from rich.align import Align
from rich.text import Text
from rich.rule import Rule
import keyring
import questionary
import psutil

# ==========================================
# 2. UI THEMING & GLOBAL INIT
# ==========================================
custom_theme = Theme({
    "info": "dim cyan",
    "warning": "bold yellow",
    "error": "bold red",
    "success": "bold green",
    "highlight": "bold magenta",
    "muted": "dim white"
})

console = Console(theme=custom_theme)

custom_qstyle = questionary.Style([
    ('qmark', 'fg:#c678dd bold'),
    ('question', 'bold'),
    ('answer', 'fg:#61afef bold'),
    ('pointer', 'fg:#c678dd bold'),
    ('highlighted', 'fg:#c678dd bold'),
    ('selected', 'fg:#98c379 bold'),
    ('disabled', 'fg:#5c6370 italic'),
])

if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
    console.print("[warning][!] DBUS_SESSION_BUS_ADDRESS not found. Keyring auth operations may fail.[/warning]")

# ==========================================
# 3. MODERN TYPE ALIASES (Python 3.12+)
# ==========================================
type ProcList = list[psutil.Process]
type ProfileList = list[str]

# ==========================================
# 3.5 GOOGLE INTEGRATION (borrowed from AntigravityManager)
# ==========================================
# The Manager refreshes the Google access token *before* account-switch injection
# (see docs/cloud_features.md 2.4 and cli/core.py:refresh_access_token), verifies
# tokens against Google's quota API, and restarts the IDE after switching
# (see switchFlow.ts). This keeps profile restores fresh instead of injecting stale
# tokens.
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Public OAuth client used by Antigravity itself (OAuthClientRegistryService.ts);
# refresh tokens are bound to this client, so refreshed tokens are accepted by the
# IDE. Extra clients can be supplied via ANTIGRAVITY_OAUTH_CLIENTS
# ("key|client_id|client_secret|label;...") and selected with
# ANTIGRAVITY_OAUTH_CLIENT_KEY, exactly like the project reads them.
OAUTH_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
OAUTH_CLIENT_SECRET = "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"
DEFAULT_OAUTH_CLIENT_KEY = "antigravity_enterprise"
OAUTH_CLIENTS_ENV = "ANTIGRAVITY_OAUTH_CLIENTS"
ACTIVE_OAUTH_CLIENT_ENV = "ANTIGRAVITY_OAUTH_CLIENT_KEY"
TOKEN_REFRESH_TIMEOUT_S = 20

# Internal Cloud Code APIs (mirrors GoogleAPIService.ts endpoint lists).
LOAD_PROJECT_ENDPOINTS = [
    "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
    "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:loadCodeAssist",
]
QUOTA_API_ENDPOINTS = [
    "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:fetchAvailableModels",
    "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
    "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
]
API_TIMEOUT_S = 20


def _platform_arch() -> str:
    """Platform/arch tags for the User-Agent (mirrors buildUserAgent in the project)."""
    plat = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        arch = "amd64"
    elif arch in ("aarch64", "arm64"):
        arch = "arm64"
    return f"{plat}/{arch}"


def build_user_agent(version: str = "2.5.0") -> str:
    """User-Agent in the same format the project sends to Google's APIs."""
    return f"antigravity/{version} {_platform_arch()}"


def is_token_expired(expiry: object) -> bool:
    """True when the ISO-8601 access-token expiry is missing, unparseable, or within 60s."""
    if not isinstance(expiry, str) or not expiry.strip():
        return True
    try:
        exp_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError:
        return True
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= exp_dt - timedelta(seconds=60)


def format_token_expiry(expires_in: int) -> str:
    """RFC-3339 expiry (UTC, milliseconds) matching the IDE's credential-store payload."""
    ts = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    return ts.isoformat(timespec="milliseconds")


def _oauth_clients() -> list[dict]:
    """Resolve OAuth clients, honoring the same env overrides the project reads."""
    clients = [{
        "key": DEFAULT_OAUTH_CLIENT_KEY,
        "client_id": OAUTH_CLIENT_ID,
        "client_secret": OAUTH_CLIENT_SECRET,
    }]
    raw = os.environ.get(OAUTH_CLIENTS_ENV, "").strip()
    if raw:
        for entry in raw.split(";"):
            parts = [part.strip() for part in entry.split("|")]
            if len(parts) < 3:
                continue
            key = parts[0].lower()
            client_id, client_secret = parts[1], parts[2]
            if not key or not client_id or not client_secret:
                continue
            existing = next((c for c in clients if c["key"] == key), None)
            if existing:
                existing.update({"client_id": client_id, "client_secret": client_secret})
            else:
                clients.append({"key": key, "client_id": client_id, "client_secret": client_secret})
    active = os.environ.get(ACTIVE_OAUTH_CLIENT_ENV, DEFAULT_OAUTH_CLIENT_KEY).strip().lower()
    active_client = next((c for c in clients if c["key"] == active), None)
    if active_client:
        return [active_client] + [c for c in clients if c["key"] != active]
    return clients


def _refresh_with_client(refresh_token: str, client_id: str, client_secret: str) -> tuple[dict | None, str, str]:
    """One refresh attempt with a specific OAuth client.

    Returns (result, status, reason); status is 'ok', 'client-mismatch' (try the
    next client), 'rejected' (stop), or 'network' (stop).
    """
    body = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    try:
        with urlopen(Request(OAUTH_TOKEN_URL, data=body, method="POST"), timeout=TOKEN_REFRESH_TIMEOUT_S) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        # Surface the OAuth error (e.g. invalid_grant = dead refresh token).
        # These error codes never contain secrets.
        reason = "unknown error"
        try:
            error_body = json.loads(e.read().decode("utf-8"))
            reason = error_body.get("error") or error_body.get("error_description") or reason
        except Exception:
            pass
        if e.code in (400, 401, 403) or "unauthorized_client" in reason or "invalid_client" in reason:
            return None, "client-mismatch", reason
        return None, "rejected", f"HTTP {e.code}: {reason}"
    except Exception as e:
        return None, "network", str(e)
    if "access_token" not in result or "expires_in" not in result:
        return None, "rejected", "malformed response"
    return result, "ok", ""


def refresh_access_token(refresh_token: str) -> dict | None:
    """Exchange an expired access token via Google's OAuth token endpoint (stdlib only).

    Tries configured OAuth clients in order, mirroring GoogleAPIService.refreshAccessToken.
    """
    last_reason = "no OAuth clients configured"
    last_status = "rejected"
    for client in _oauth_clients():
        result, status, reason = _refresh_with_client(
            refresh_token, client["client_id"], client["client_secret"]
        )
        if result is not None:
            return result
        last_reason = reason
        last_status = status
        if status != "client-mismatch":
            break
    label = "failed (network)" if last_status == "network" else "rejected"
    console.print(f"[warning]! Token refresh {label}: {last_reason}[/warning]")
    return None


def fetch_available_models(access_token: str, timeout_s: int = API_TIMEOUT_S) -> dict:
    """Fetch live model quota from Google's internal API (mirrors fetchQuota).

    Returns {'models': {name: percent}} on success, or {'error': reason, 'auth': bool}
    on failure ('auth' distinguishes token rejection from transient API trouble).
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": build_user_agent(),
        "Content-Type": "application/json",
    }
    project_id: str | None = None
    for endpoint in LOAD_PROJECT_ENDPOINTS:
        try:
            with urlopen(
                Request(
                    endpoint,
                    data=json.dumps({"metadata": {"ideType": "ANTIGRAVITY"}}).encode("utf-8"),
                    headers=headers,
                    method="POST",
                ),
                timeout=timeout_s,
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            project_id = data.get("cloudaicompanionProject")
            if project_id:
                break
        except Exception:
            continue

    payload_dict: dict = {"project": project_id} if project_id else {}
    last_error: str | None = None
    last_auth = False
    for endpoint in QUOTA_API_ENDPOINTS:
        # Mirror GoogleAPIService.fetchQuota: a 403 with a project ID attached is
        # retried on the same endpoint without the project before moving on.
        attempts = [payload_dict] + ([{}] if project_id else [])
        for attempt_payload in attempts:
            try:
                with urlopen(
                    Request(
                        endpoint,
                        data=json.dumps(attempt_payload).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    ),
                    timeout=timeout_s,
                ) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                models: dict[str, int] = {}
                for name, info in (raw.get("models") or {}).items():
                    quota_info = info.get("quotaInfo") or {}
                    fraction = quota_info.get("remainingFraction")
                    if isinstance(fraction, (int, float)):
                        models[name] = max(0, min(100, int(fraction * 100)))
                if not models:
                    last_error = "no quota info in response"
                    last_auth = False
                    continue
                return {"models": models}
            except HTTPError as e:
                if e.code == 401:
                    return {"error": "HTTP 401 (token rejected or forbidden)", "auth": True}
                if e.code == 403:
                    last_error = "HTTP 403 (token rejected or forbidden)"
                    last_auth = True
                    continue
                last_error = f"HTTP {e.code}"
                last_auth = False
                continue
            except Exception as e:
                last_error = str(e)
                last_auth = False
                continue
    return {"error": last_error or "failed", "auth": last_auth}


def find_antigravity_executable() -> str | None:
    """Locate the Antigravity launcher or binary.

    Priority: AGM_ANTIGRAVITY_BIN > gui_config.json > shutil.which("antigravity")
    > known Linux install paths.
    """
    env_path = os.environ.get("AGM_ANTIGRAVITY_BIN", "").strip()
    if env_path and os.path.exists(env_path):
        return env_path
    try:
        config_path = Path.home() / ".antigravity-agent" / "gui_config.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        exe = str(data.get("antigravity_executable") or "").strip()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    from_path = shutil.which("antigravity")
    if from_path:
        return from_path
    for candidate in (
        str(Path.home() / ".local" / "bin" / "antigravity"),
        "/opt/Antigravity-x64/antigravity",
        "/opt/Antigravity/antigravity",
        "/usr/local/bin/antigravity",
        "/usr/bin/antigravity",
        "/usr/share/antigravity/antigravity",
        str(Path.home() / ".local" / "share" / "antigravity" / "antigravity"),
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def start_antigravity() -> bool:
    """Relaunch Antigravity detached from the terminal session."""
    exe = find_antigravity_executable()
    if not exe:
        console.print("[warning]! Could not locate the Antigravity executable to relaunch.[/warning]")
        return False
    args: list[str] = []
    try:
        config_data = json.loads(
            (Path.home() / ".antigravity-agent" / "gui_config.json").read_text(encoding="utf-8")
        )
        raw_args = config_data.get("antigravity_args")
        if isinstance(raw_args, list):
            args = [str(arg) for arg in raw_args]
    except Exception:
        pass
    try:
        # start_new_session=True (setsid) & close_fds=True ensures complete terminal detachment
        subprocess.Popen(
            [exe, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        console.print(f"[success]✓ Relaunched Antigravity ({exe}) [detached].[/success]")
        return True
    except Exception as e:
        console.print(f"[error]✗ Failed to relaunch Antigravity: {e}[/error]")
        return False


# ==========================================
# 4. CORE MANAGER CLASS
# ==========================================
class ProfileManager:
    def __init__(self, force_mode: bool = False, restart_mode: bool = False) -> None:
        self.force_mode = force_mode
        self.restart_mode = restart_mode
        # Environment overrides enable relocating storage and sandboxed testing.
        self.storage_dir = Path(
            os.environ.get("AGM_STORAGE_DIR")
            or (Path.home() / ".config" / "dusky" / "settings" / "apps" / "antigravity")
        )
        self.profiles_dir = self.storage_dir / "profiles"
        self.active_profile_file = self.profiles_dir / "active_profile.txt"
        self.order_file = self.profiles_dir / "profile_order.txt"
        self.service = os.environ.get("AGM_KEYRING_SERVICE", "gemini")
        self.account = os.environ.get("AGM_KEYRING_ACCOUNT", "antigravity")
        
        try:
            self.profiles_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            console.print(f"[error]✗ Fatal: Filesystem constraint preventing directory creation in {self.storage_dir}: {e}[/error]")
            sys.exit(1)

    @staticmethod
    def is_valid_name(name: str) -> bool:
        """Strict alphanumeric, dash, and underscore validation."""
        return bool(re.match(r"^[a-zA-Z0-9_][a-zA-Z0-9_-]*$", name))

    def get_active(self) -> str | None:
        if self.active_profile_file.is_file():
            try:
                name = self.active_profile_file.read_text(encoding="utf-8").strip()
                # Security: Prevent path traversal by validating the string
                if name and self.is_valid_name(name) and (self.profiles_dir / name).is_dir():
                    return name
            except IOError as e:
                console.print(f"[warning]! State read error: {e}[/warning]")
        return None

    def _read_order(self) -> list[str]:
        """Read the persisted display order (one profile name per line), if any."""
        try:
            if not self.order_file.is_file():
                return []
            return [
                line.strip()
                for line in self.order_file.read_text(encoding="utf-8").splitlines()
                if line.strip() and self.is_valid_name(line.strip())
            ]
        except (IOError, OSError):
            return []

    def _persist_order(self) -> None:
        """Persist the current effective display order to the order file."""
        try:
            self.order_file.write_text("\n".join(self.get_all()) + "\n", encoding="utf-8")
        except (IOError, OSError) as e:
            console.print(f"[warning]! Could not persist profile order: {e}[/warning]")

    def get_all(self) -> ProfileList:
        try:
            # Security: Filter out invalid directories (e.g. backup folders)
            dirs = [p.name for p in self.profiles_dir.iterdir() if p.is_dir() and self.is_valid_name(p.name)]
            # Ordered list from the order file (filtered to existing profiles, deduped)
            ordered = list(dict.fromkeys(name for name in self._read_order() if name in dirs))
            # Self-heal: append any profiles not listed yet (e.g. imported/created), sorted
            missing = sorted(set(dirs) - set(ordered))
            return ordered + missing
        except (IOError, OSError):
            return []

    def _get_token_path(self, profile_name: str) -> Path:
        """Resolve token path and silently migrate legacy .json extensions to .txt"""
        legacy = self.profiles_dir / profile_name / "keyring_token.json"
        txt = self.profiles_dir / profile_name / "keyring_token.txt"
        if legacy.exists() and not txt.exists():
            try:
                legacy.rename(txt)
            except OSError:
                pass
        return txt

    def check_running_processes(self) -> ProcList:
        """Detect active Antigravity processes, excluding this script and shell ancestors."""
        procs: ProcList = []
        current_pid = os.getpid()
        parent_pid = os.getppid()
        
        try:
            grandparent_pid = psutil.Process(parent_pid).ppid()
        except psutil.Error:
            grandparent_pid = -1
            
        exclude_pids = {current_pid, parent_pid, grandparent_pid}
        target_names = {"antigravity", "agy", "antigravity-cli", "antigravity-ide"}
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'exe']):
            try:
                if proc.info['pid'] in exclude_pids:
                    continue
                    
                name = (proc.info['name'] or "").lower()
                exe_path = (proc.info.get('exe') or "").lower()
                exe_name = Path(exe_path).name.lower() if exe_path else ""
                cmdline = proc.info.get('cmdline') or []

                is_match = False
                if name in target_names or exe_name in target_names:
                    is_match = True
                elif "antigravity" in exe_path and not any("switch_accounts" in arg for arg in cmdline):
                    is_match = True
                elif cmdline:
                    first_arg_base = Path(cmdline[0]).name.lower()
                    if first_arg_base in target_names and "switch_accounts" not in first_arg_base:
                        is_match = True

                if is_match:
                    procs.append(proc)
            except psutil.Error:
                pass
        return procs

    def kill_processes(self, processes: ProcList) -> bool:
        """Safely terminate blocking processes with SIGTERM then SIGKILL."""
        if not processes:
            return True
        for proc in processes:
            try:
                proc.terminate()
            except psutil.Error:
                continue

        gone, alive = psutil.wait_procs(processes, timeout=3.0)
        for proc in alive:
            try:
                proc.kill()
            except psutil.Error:
                pass
        if alive:
            psutil.wait_procs(alive, timeout=1.0)
        console.print(f"[success]✓ Closed {len(processes)} conflicting Antigravity process(es).[/success]")
        return True

    def stash_keyring(self, profile_name: str) -> bool:
        """Save active keyring credentials to the profile directory."""
        try:
            token = keyring.get_password(self.service, self.account)
            token_file = self._get_token_path(profile_name)
            if token:
                token_file.touch(mode=0o600, exist_ok=True)
                token_file.write_text(token, encoding="utf-8")
                token_file.chmod(0o600)
                console.print(f"[info]› Secured auth token to '{profile_name}'.[/info]")
            else:
                console.print(f"[warning]! OS keyring returned no credentials for {self.service}/{self.account}; nothing stashed.[/warning]")
            return True
        except Exception as e:
            console.print(f"[warning]! Credential stash failure: {e}[/warning]")
            return False

    def restore_keyring(self, profile_name: str) -> bool:
        """Inject stored profile credentials into OS keyring (instant local operation)."""
        token_file = self._get_token_path(profile_name)
        if token_file.is_file():
            try:
                token = token_file.read_text(encoding="utf-8").strip()
                if not token:
                    console.print(f"[warning]! Empty credential payload in '{profile_name}'. Skipping restore.[/warning]")
                    return True
                keyring.set_password(self.service, self.account, token)
                console.print(f"[info]✓ Restored auth credentials for '{profile_name}'.[/info]")
                return True
            except Exception as e:
                console.print(f"[error]✗ Credential restore failure: {e}[/error]")
                return False
        else:
            try:
                keyring.delete_password(self.service, self.account)
                console.print("[info]› Initialized fresh global auth state.[/info]")
            except Exception:
                pass
            return True

    def _maybe_refresh_token(self, profile_name: str, raw: str) -> str:
        """Helper used exclusively by check_profile to refresh expired tokens for diagnostics."""
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return raw
        if not isinstance(payload, dict):
            return raw
        token = payload.get("token")
        if not isinstance(token, dict):
            return raw
        refresh_token = token.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            return raw
        if not is_token_expired(token.get("expiry")):
            return raw
        console.print(f"[info]› Refreshing token for '{profile_name}' via Google...[/info]")
        result = refresh_access_token(refresh_token)
        if result is None:
            console.print(f"[warning]! Could not refresh token for '{profile_name}'.[/warning]")
            return raw
        token["access_token"] = result["access_token"]
        token["expiry"] = format_token_expiry(result["expires_in"])
        fresh_refresh = result.get("refresh_token")
        if isinstance(fresh_refresh, str) and fresh_refresh:
            token["refresh_token"] = fresh_refresh
        fresh_id = result.get("id_token")
        if isinstance(fresh_id, str) and fresh_id:
            payload["id_token"] = fresh_id
        console.print(f"[success]✓ Successfully refreshed token for '{profile_name}'.[/success]")
        return json.dumps(payload, separators=(",", ":"))

    def check_profile(self, profile_name: str) -> bool:
        """Validate a profile's token against Google and display live model quota."""
        if not self.is_valid_name(profile_name):
            console.print(f"[error]✗ Error: Invalid profile syntax '{profile_name}'.[/error]")
            return False
        token_file = self._get_token_path(profile_name)
        if not token_file.is_file():
            console.print(f"[error]✗ Profile '{profile_name}' has no stored credentials.[/error]")
            return False
        try:
            raw = token_file.read_text(encoding="utf-8").strip()
            refreshed = self._maybe_refresh_token(profile_name, raw)
            if refreshed != raw:
                token_file.touch(mode=0o600, exist_ok=True)
                token_file.write_text(refreshed, encoding="utf-8")
                token_file.chmod(0o600)
                if profile_name == self.get_active():
                    keyring.set_password(self.service, self.account, refreshed)
                console.print(f"[success]✓ Stash for '{profile_name}' updated with refreshed token.[/success]")
            payload = json.loads(refreshed)
            token = payload.get("token") if isinstance(payload, dict) else None
            access_token = token.get("access_token") if isinstance(token, dict) else None
            if not isinstance(access_token, str) or not access_token:
                console.print(f"[error]✗ No access token available for '{profile_name}'.[/error]")
                return False
            result = fetch_available_models(access_token)
            if "error" in result:
                if result.get("auth"):
                    console.print(f"[error]✗ Token rejected by Google for '{profile_name}': {result['error']}[/error]")
                    return False
                console.print(f"[warning]! Could not reach Google's quota API for '{profile_name}' ({result['error']}); token status unknown.[/warning]")
                return True
            models = result["models"]
            if not models:
                console.print(f"[warning]! '{profile_name}' verified but returned no model quota info.[/warning]")
                return True
            table = Table(title=f"Live Quota: {profile_name}", title_style="bold magenta", border_style="cyan", box=box.ROUNDED, expand=False, padding=(0, 2))
            table.add_column("Model", style="bold cyan")
            table.add_column("Remaining", justify="right")
            for name in sorted(models):
                pct = models[name]
                style = "bold green" if pct >= 50 else ("bold yellow" if pct >= 20 else "bold red")
                table.add_row(name, Text(f"{pct}%", style=style))
            console.print("")
            console.print(Align.center(table))
            console.print("")
            console.print(f"[success]✓ '{profile_name}' verified against Google.[/success]")
            return True
        except Exception as e:
            console.print(f"[error]✗ Verification error for '{profile_name}': {e}[/error]")
            return False

    def switch(self, target_profile: str) -> bool:
        """Switch to the specified profile. Instantaneous and non-blocking."""
        if not self.is_valid_name(target_profile):
            console.print(f"[error]✗ Error: Invalid profile syntax '{target_profile}'.[/error]")
            return False

        current_profile = self.get_active()
        if current_profile == target_profile:
            console.print(f"[info]› State unchanged. Already on '{target_profile}'.[/info]")
            if self.restart_mode:
                running_procs = self.check_running_processes()
                if running_procs:
                    console.print(f"[warning]! {len(running_procs)} Antigravity process(es) running — closing for restart...[/warning]")
                    self.kill_processes(running_procs)
                start_antigravity()
            return True

        running_procs = self.check_running_processes()
        should_relaunch = self.restart_mode

        if running_procs:
            if self.restart_mode:
                console.print(f"[warning]! {len(running_procs)} Antigravity process(es) running — closing for restart...[/warning]")
                self.kill_processes(running_procs)
                should_relaunch = True
            elif self.force_mode:
                console.print("[warning]! Force override active: Bypassing process collision checks.[/warning]")
            elif not sys.stdin.isatty():
                console.print("\n[error]✗ Active Antigravity processes detected in non-interactive mode. Aborting switch to prevent background hang. Use -f/--force or -r/--restart.[/error]")
                return False
            else:
                console.print(f"\n[warning]! {len(running_procs)} Active Antigravity process(es) detected![/warning]")
                action = questionary.select(
                    "Resolve collision:",
                    choices=[
                        questionary.Choice("Kill & Relaunch (Recommended)", value="kill_relaunch"),
                        questionary.Choice("Kill without Relaunch", value="kill"),
                        questionary.Choice("Ignore & Proceed (Risky)", value="ignore"),
                        questionary.Choice("Abort (Safe)", value="cancel"),
                    ],
                    default="kill_relaunch",
                    pointer="❯",
                    style=custom_qstyle
                ).ask()
                
                match action:
                    case "cancel" | None:
                        console.print("[error]Operation aborted.[/error]")
                        return False
                    case "kill_relaunch":
                        self.kill_processes(running_procs)
                        should_relaunch = True
                    case "kill":
                        self.kill_processes(running_procs)
                        should_relaunch = False
                    case "ignore":
                        console.print("[warning]Proceeding with collision risk...[/warning]")

        if current_profile and not self.stash_keyring(current_profile):
            return False

        target_path = self.profiles_dir / target_profile
        try:
            target_path.mkdir(parents=True, exist_ok=True)
            if not self.restore_keyring(target_profile):
                return False
            self.active_profile_file.write_text(target_profile, encoding="utf-8")
        except IOError as e:
            console.print(f"[error]✗ IO fault during state switch: {e}[/error]")
            return False
            
        console.print(f"\n[success]✓ Switched to profile: '{target_profile}'.[/success]")
        if should_relaunch:
            start_antigravity()
        return True

    def cycle_next(self) -> bool:
        profiles = self.get_all()
        if not profiles:
            console.print("[error]✗ Error: Array is empty. No profiles to cycle.[/error]")
            return False
            
        active = self.get_active()
        next_profile = profiles[0] if active not in profiles else profiles[(profiles.index(active) + 1) % len(profiles)]
            
        console.print(f"\n[info]› Iterating to next profile ({next_profile})...[/info]")
        return self.switch(next_profile)

    def create(self, name: str, switch_now: bool | None = None) -> bool:
        if not self.is_valid_name(name):
            console.print("[error]✗ Syntax Error: Alphanumeric, dash, and underscores exclusively.[/error]")
            return False
            
        profile_path = self.profiles_dir / name
        if profile_path.is_dir():
            console.print(f"[error]✗ Collision: Profile '{name}' already exists.[/error]")
            return False
            
        try:
            profile_path.mkdir(parents=True)
            console.print(f"[success]✓ Initialized isolated context: '{name}'.[/success]")
            self._persist_order()
            should_switch = switch_now if switch_now is not None else (
                sys.stdin.isatty() and questionary.confirm("Execute context switch to new profile now?", style=custom_qstyle).ask()
            )
            if should_switch:
                self.switch(name)
            return True
        except OSError as e:
            console.print(f"[error]✗ IO Error during initialization: {e}[/error]")
            return False

    def delete(self, name: str, confirm: bool | None = None) -> bool:
        if name == self.get_active():
            console.print("[error]✗ State lock: Cannot delete the active profile. Cycle first.[/error]")
            return False
            
        profile_path = self.profiles_dir / name
        if not profile_path.is_dir():
            console.print(f"[error]✗ Missing Reference: '{name}' does not exist.[/error]")
            return False
            
        should_delete = confirm if confirm is not None else (
            sys.stdin.isatty() and questionary.confirm(f"Permanently wipe '{name}' and all isolated data?", style=custom_qstyle).ask()
        )
        if should_delete:
            try:
                shutil.rmtree(profile_path)
                console.print(f"[success]✓ Profile '{name}' removed.[/success]")
                self._persist_order()
                return True
            except OSError as e:
                console.print(f"[error]✗ IO Fault during deletion: {e}[/error]")
                return False
        return False

    def rename(self, old_name: str, new_name: str) -> bool:
        """Rename a saved profile (directory plus active marker if applicable)."""
        if old_name == new_name:
            console.print(f"[info]› New name identical to current name.[/info]")
            return False
        if not self.is_valid_name(old_name) or not self.is_valid_name(new_name):
            console.print("[error]✗ Syntax Error: Alphanumeric, dash, and underscores exclusively.[/error]")
            return False
        old_path = self.profiles_dir / old_name
        if not old_path.is_dir():
            console.print(f"[error]✗ Missing Reference: '{old_name}' does not exist.[/error]")
            return False
        new_path = self.profiles_dir / new_name
        if new_path.is_dir():
            console.print(f"[error]✗ Collision: Profile '{new_name}' already exists.[/error]")
            return False
            
        was_active = self.get_active() == old_name
        ordered = self._read_order()
        try:
            old_path.rename(new_path)
            if was_active:
                self.active_profile_file.write_text(new_name, encoding="utf-8")
            if ordered:
                if old_name in ordered:
                    ordered = [new_name if name == old_name else name for name in ordered]
                else:
                    ordered = ordered + [new_name]
                self.order_file.write_text("\n".join(ordered) + "\n", encoding="utf-8")
            console.print(f"[success]✓ Profile '{old_name}' renamed to '{new_name}'.[/success]")
            return True
        except OSError as e:
            console.print(f"[error]✗ IO Fault during rename: {e}[/error]")
            return False

    def reorder(self, name: str, direction: str) -> bool:
        """Move a profile up or down in the display/cycle order."""
        profiles = self.get_all()
        if name not in profiles:
            console.print(f"[error]✗ Missing Reference: '{name}' does not exist.[/error]")
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
            console.print(f"[error]✗ Invalid direction: '{direction}'. Use 'up' or 'down'.[/error]")
            return False
        try:
            self.order_file.write_text("\n".join(profiles) + "\n", encoding="utf-8")
            console.print(f"[success]✓ Moved '{name}' {direction} (position {profiles.index(name) + 1}).[/success]")
            return True
        except (IOError, OSError) as e:
            console.print(f"[error]✗ IO Fault during reorder: {e}[/error]")
            return False

    def render_dashboard(self) -> None:
        active = self.get_active()
        profiles = self.get_all()
        
        table = Table(
            title="Local Isolation Matrix",
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
        table.add_column("Profile Name", style="bold white", no_wrap=True)
        table.add_column("Status", justify="center", no_wrap=True)
        
        for idx, p in enumerate(profiles, start=1):
            is_active = p == active
            status_text = Text("● ACTIVE", style="bold green") if is_active else Text("○ STANDBY", style="dim white")
            
            token_file = self._get_token_path(p)
            auth_state = Text("Void", style="dim yellow")
            if token_file.is_file() and token_file.stat().st_size > 0:
                auth_state = Text("Secured", style="bold cyan")
            
            table.add_row(str(idx), status_text, p, auth_state)
            
        if not profiles:
            console.print(Align.center("[muted]No profiles found. Create a profile to begin.[/muted]"))
        else:
            console.print(Align.center(table))


# ==========================================
# 5. ROUTER & EVENT LOOP
# ==========================================
CANCEL_VALUE = "← Cancel / Go Back"
DONE_VALUE = "✓ Done"


def build_profile_choices(
    profiles: ProfileList,
    active_profile: str | None = None,
    lock_active: bool = False,
    indicate_active: bool = True
) -> list[questionary.Choice]:
    choices = []
    for p in profiles:
        is_active = (p == active_profile)
        if lock_active and is_active:
            choices.append(questionary.Choice(f"{p} (Active - Locked)", value=p, disabled="Cannot delete active profile"))
        elif indicate_active and is_active:
            choices.append(questionary.Choice(f"{p} (Active)", value=p))
        else:
            choices.append(questionary.Choice(p, value=p))
    choices.append(questionary.Choice(CANCEL_VALUE, value=CANCEL_VALUE))
    return choices


def render_screen(manager: ProfileManager) -> None:
    """Clear screen and display the centered header and compact dashboard."""
    console.clear()
    title = Text("◆ Antigravity Profile Manager", style="bold magenta")
    subtitle = Text("Account Isolation & Credentials Switcher", style="dim cyan")
    header = Panel(
        Align.center(Text.assemble(title, "\n", subtitle)),
        border_style="magenta",
        box=box.ROUNDED,
        expand=False,
        padding=(0, 3)
    )
    console.print("")
    console.print(Align.center(header))
    console.print("")
    manager.render_dashboard()
    console.print("")


def interactive_tui(manager: ProfileManager) -> None:
    while True:
        render_screen(manager)
        
        profiles = manager.get_all()
        active = manager.get_active()
        main_choices = []
        
        if profiles:
            main_choices.append(questionary.Choice("Switch Profile", value="switch"))
            main_choices.append(questionary.Choice("Cycle to Next Profile", value="cycle"))
            main_choices.append(questionary.Choice("Check Profile Quota", value="check"))
        
        main_choices.extend([
            questionary.Choice("Relaunch Antigravity", value="relaunch"),
            questionary.Choice("Create New Profile", value="create"),
            questionary.Choice("Delete Profile", value="delete", disabled="No profiles created" if not profiles else ("Cannot delete the only active profile" if len(profiles) == 1 and active in profiles else None)),
            questionary.Choice("Rename Profile", value="rename", disabled="No profiles created" if not profiles else None),
            questionary.Choice("Reorder Profiles", value="reorder", disabled="Need at least two profiles" if len(profiles) < 2 else None),
            questionary.Choice("Backup/Save Credentials", value="stash", disabled="No active profile" if not active else None),
            questionary.Choice("Quit", value="quit")
        ])

        try:
            action = questionary.select(
                "Select Action:",
                choices=main_choices,
                pointer="❯",
                style=custom_qstyle
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
                        "Select profile to switch to:", 
                        choices=build_profile_choices(profiles, active_profile=active),
                        default=active if active in profiles else None,
                        pointer="❯",
                        style=custom_qstyle
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
                        "Select profile to check quota:", 
                        choices=build_profile_choices(profiles, active_profile=active),
                        default=active if active in profiles else None,
                        pointer="❯",
                        style=custom_qstyle
                    ).ask()
                    
                    if target and target != CANCEL_VALUE:
                        manager.check_profile(target)
                        questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()
                case "relaunch":
                    start_antigravity()
                    questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()
                case "create":
                    name = questionary.text("Enter name for new profile (leave blank to cancel):", style=custom_qstyle).ask()
                    
                    if name and name.strip(): 
                        manager.create(name.strip())
                        questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()
                case "delete":
                    target = questionary.select(
                        "Select profile to delete:", 
                        choices=build_profile_choices(profiles, active_profile=active, lock_active=True),
                        default=next((p for p in profiles if p != active), None),
                        pointer="❯",
                        style=custom_qstyle
                    ).ask()
                    
                    if target and target != CANCEL_VALUE: 
                        manager.delete(target)
                        questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()
                case "rename":
                    target = questionary.select(
                        "Select profile to rename:", 
                        choices=build_profile_choices(profiles, active_profile=active),
                        default=active if active in profiles else None,
                        pointer="❯",
                        style=custom_qstyle
                    ).ask()
                    
                    if target and target != CANCEL_VALUE:
                        new_name = questionary.text(
                            "Enter new name for profile (leave blank to cancel):",
                            validate=lambda v: v.strip() == "" or manager.is_valid_name(v.strip())
                            or "Invalid name: alphanumeric, dashes, and underscores only",
                            style=custom_qstyle
                        ).ask()
                        if new_name and new_name.strip():
                            manager.rename(target, new_name.strip())
                        questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()
                case "reorder":
                    target = questionary.select(
                        "Select profile to move:", 
                        choices=build_profile_choices(profiles, active_profile=active),
                        default=active if active in profiles else None,
                        pointer="❯",
                        style=custom_qstyle
                    ).ask()
                    
                    if target and target != CANCEL_VALUE:
                        while True:
                            move_action = questionary.select(
                                f"Move '{target}' where?",
                                choices=[
                                    questionary.Choice("↑ Move Up", value="up"),
                                    questionary.Choice("↓ Move Down", value="down"),
                                    questionary.Choice(DONE_VALUE, value=DONE_VALUE)
                                ],
                                pointer="❯",
                                style=custom_qstyle
                            ).ask()
                            if move_action is None or move_action == DONE_VALUE:
                                break
                            if manager.reorder(target, move_action):
                                render_screen(manager)
                case "stash":
                    active_profile = manager.get_active()
                    if active_profile:
                        manager.stash_keyring(active_profile)
                        questionary.press_any_key_to_continue("\nPress any key to return...", style=custom_qstyle).ask()
        except KeyboardInterrupt:
            continue


def main() -> None:
    parser = argparse.ArgumentParser(description="Antigravity Profile Manager & Credentials Switcher")
    parser.add_argument("profile", nargs="?", help="Direct profile override")
    parser.add_argument("-l", "--list", action="store_true", help="List all available profiles and exit")
    parser.add_argument("-n", "--next", action="store_true", help="Cycle to the next profile and exit")
    parser.add_argument("-f", "--force", action="store_true", help="Bypass running process check and force switch non-interactively")
    parser.add_argument("-r", "--restart", action="store_true", help="Close Antigravity if running, switch profile, then relaunch it detached")
    parser.add_argument("-c", "--check", nargs="?", const="__active__", metavar="PROFILE", help="Validate a profile's token and verify live quota against Google (defaults to active profile)")
    parser.add_argument("--launch", "--relaunch", action="store_true", help="Launch or relaunch Antigravity detached from the terminal and exit")
    
    args = parser.parse_args()

    manager = ProfileManager(force_mode=args.force, restart_mode=args.restart)

    if args.launch or getattr(args, 'relaunch', False):
        if not start_antigravity():
            sys.exit(1)
    elif args.list:
        manager.render_dashboard()
    elif args.check is not None:
        target = args.check if args.check != "__active__" else (args.profile or manager.get_active())
        if not target:
            console.print("[error]✗ No active profile to check.[/error]")
            sys.exit(1)
        if not manager.check_profile(target):
            sys.exit(1)
    elif args.next:
        if not manager.cycle_next():
            sys.exit(1)
    elif args.profile:
        if not manager.switch(args.profile):
            sys.exit(1)
    else:
        if not sys.stdin.isatty():
            console.print("[error]✗ Interactive mode requires a terminal. Use -l, -n, -f, -r, -c, --launch, or a profile name instead.[/error]")
            sys.exit(1)
        interactive_tui(manager)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[error]Process killed via SIGINT.[/error]")
        sys.exit(130)
