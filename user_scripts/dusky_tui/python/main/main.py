#!/usr/bin/env python3
import sys
import os

# Enable bytecode caching for faster startup.
sys.dont_write_bytecode = False
os.environ.pop("PYTHONDONTWRITEBYTECODE", None)

import argparse
import importlib.util
import json
import shutil
import logging
import hashlib
import pwd
import subprocess
import shlex
import atexit
import tempfile
import re
import socket
import struct
from datetime import datetime
from pathlib import Path


# =============================================================================
# REAL-USER ENVIRONMENT RECONSTRUCTION & FAST PERMISSIONS (SUDO/PKEXEC SAFETY)
# =============================================================================
if os.geteuid() == 0:
    _real_uid = None
    _real_gid = None

    _sudo_user = os.environ.get("SUDO_USER")
    _pkexec_uid = os.environ.get("PKEXEC_UID")

    if _sudo_user and _sudo_user != "root":
        try:
            _pw = pwd.getpwnam(_sudo_user)
            _real_uid = _pw.pw_uid
            _real_gid = _pw.pw_gid
        except KeyError:
            pass

    elif _pkexec_uid:
        try:
            _pw = pwd.getpwuid(int(_pkexec_uid))
            _real_uid = _pw.pw_uid
            _real_gid = _pw.pw_gid
        except Exception:
            pass

    if _real_uid and _real_gid:
        try:
            _pw = pwd.getpwuid(_real_uid)

            os.environ["HOME"] = _pw.pw_dir
            os.environ["USER"] = _pw.pw_name

            # Guarantee XDG base directories point to the real user.
            for xdg_var, default_suffix in [
                ("XDG_CONFIG_HOME", ".config"),
                ("XDG_CACHE_HOME", ".cache"),
                ("XDG_DATA_HOME", ".local/share"),
                ("XDG_STATE_HOME", ".local/state")
            ]:
                if xdg_var not in os.environ or os.environ[xdg_var].startswith("/root"):
                    os.environ[xdg_var] = os.path.join(_pw.pw_dir, default_suffix)

            def _fast_chown_tree(dir_path: str) -> None:
                """Zero-allocation tree traversal using raw os.scandir string paths."""
                try:
                    stat_res = os.lstat(dir_path)
                    if stat_res.st_uid == 0:
                        os.chown(dir_path, _real_uid, _real_gid, follow_symlinks=False)

                    with os.scandir(dir_path) as it:
                        for entry in it:
                            try:
                                if entry.stat(follow_symlinks=False).st_uid == 0:
                                    os.chown(entry.path, _real_uid, _real_gid, follow_symlinks=False)
                                if entry.is_dir(follow_symlinks=False):
                                    _fast_chown_tree(entry.path)
                            except OSError:
                                pass
                except OSError:
                    pass

            def _fix_permissions():
                try:
                    home = _pw.pw_dir

                    def _xdg_path(var: str, suffix: str) -> str:
                        val = os.environ.get(var, "").strip()
                        return os.path.expanduser(val) if val else os.path.join(home, suffix)

                    targets = [
                        os.path.join(_xdg_path("XDG_CONFIG_HOME", ".config"), "dusky"),
                        os.path.join(_xdg_path("XDG_CACHE_HOME", ".cache"), "dusky_tui"),
                        os.path.join(_xdg_path("XDG_STATE_HOME", ".local/state"), "dusky", "logs"),
                        os.path.join(_xdg_path("XDG_DATA_HOME", ".local/share"), "dusky_backups"),
                        os.path.join(home, "Documents", "logs", "tui"),
                        os.path.join(home, "Documents", "dusky_backups", "tui_reset"),
                    ]

                    for target in targets:
                        if os.path.exists(target):
                            _fast_chown_tree(target)

                except Exception:
                    pass

            _fix_permissions()
            atexit.register(_fix_permissions)

        except KeyError:
            pass


# =============================================================================
# CACHE & IOC SETUP
# =============================================================================
def _setup_cache() -> None:
    try:
        xdg_cache_env = os.environ.get("XDG_CACHE_HOME", "").strip()
        xdg_cache = (
            Path(xdg_cache_env).expanduser().resolve()
            if xdg_cache_env
            else Path.home() / ".cache"
        )

        cache_dir = xdg_cache / "dusky_tui"
        cache_dir.mkdir(parents=True, exist_ok=True)

        sys.pycache_prefix = str(cache_dir)

    except OSError:
        pass


_setup_cache()


PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SCHEMA_SEARCH_PATHS = [
    Path("~/user_scripts").expanduser().resolve(),
    Path("~/.config/dusky_schema").expanduser().resolve(),
    Path("~/Documents/schemas").expanduser().resolve(),
]


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================
def setup_logging(module_name: str, enable_logging: bool) -> logging.Logger:
    """Configures logging. Attaches a NullHandler if disabled to prevent TUI corruption."""
    logger = logging.getLogger("dusky_router")
    logger.setLevel(logging.DEBUG if enable_logging else logging.WARNING)

    if enable_logging:
        log_dir = Path("~/Documents/logs/tui/").expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / f"{module_name}_runner.log"

        fh = logging.FileHandler(log_file)
        fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s"))
        logger.addHandler(fh)

        print(f"[*] Logging enabled: {log_file}")

    else:
        logger.addHandler(logging.NullHandler())

    return logger


def manage_backup(target_file: Path, action: str, logger: logging.Logger) -> bool:
    """Handles creating and restoring backups across multi-file ecosystems with atomic replace & fsync."""
    backup_dir = Path("~/Documents/dusky_backups/tui_reset/").expanduser()
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        backup_dir.chmod(0o700)
    except OSError:
        pass

    resolved = target_file.expanduser().resolve()
    path_hash = hashlib.blake2b(str(resolved).encode(), digest_size=10).hexdigest()
    parent_bits = "_".join(resolved.parent.parts[-2:]) if resolved.parent.parts else "root"
    parent_bits = re.sub(r"[^\w.-]+", "_", parent_bits)[:64]
    stem = f"{parent_bits}_{path_hash}_{resolved.name}"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{stem}.{timestamp}.bak"
    latest_link = backup_dir / f"{stem}.latest.bak"

    def atomic_copy(src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out_f, src.open("rb") as in_f:
                while chunk := in_f.read(1024 * 1024):
                    out_f.write(chunk)
                out_f.flush()
                os.fsync(out_f.fileno())
            os.replace(tmp_path, dest)
            dir_fd = os.open(str(dest.parent), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    match action:
        case "check_restore":
            if not latest_link.exists():
                print(f"[-] Missing backup for: {resolved.name}")
                return False
            return True

        case "restore":
            if not latest_link.exists():
                return False
            actual = latest_link.resolve(strict=True)
            atomic_copy(actual, resolved)
            print(f"[+] Restored: {resolved} from {actual.name}")
            logger.info("Restored %s from %s", resolved, actual)
            return True

        case "create":
            if not resolved.exists():
                return False
            atomic_copy(resolved, backup_path)
            tmp_link = backup_dir / f".{stem}.latest.bak.tmp-{os.getpid()}"
            if tmp_link.exists() or tmp_link.is_symlink():
                tmp_link.unlink()
            tmp_link.symlink_to(backup_path.resolve())
            os.replace(tmp_link, latest_link)
            print(f"[+] Backup created: {backup_path.name}")
            logger.info("Created backup for %s at %s", resolved, backup_path)
            return True

        case _:
            return False


# =============================================================================
# LAZY ENGINE POOL FACTORY
# =============================================================================
class LazyEnginePool(dict):
    """
    Lazy initialization factory dict. Engines are instantiated ONLY on active lookup.
    """
    def __init__(self, factory_func):
        super().__init__()
        self._factory = factory_func
        self._registered_keys: set[tuple[str, str]] = set()

    def register(self, e_type: str, config_path: str) -> tuple[str, str]:
        key = (e_type, config_path)
        self._registered_keys.add(key)
        return key

    def __getitem__(self, key: tuple[str, str]):
        if not super().__contains__(key):
            self[key] = self._factory(key[0], key[1])
        return super().__getitem__(key)

    def get(self, key: tuple[str, str], default=None):
        if super().__contains__(key):
            return super().__getitem__(key)
        if key in self._registered_keys:
            try:
                return self[key]
            except Exception:
                return default
        return default

    def __contains__(self, key: object) -> bool:
        return super().__contains__(key) or key in self._registered_keys

    def values(self):
        for key in list(self._registered_keys):
            self[key]
        return super().values()

    def items(self):
        for key in list(self._registered_keys):
            self[key]
        return super().items()

    def keys(self):
        for key in list(self._registered_keys):
            self[key]
        return super().keys()

    def __iter__(self):
        for key in list(self._registered_keys):
            self[key]
        return super().__iter__()

    def __len__(self):
        return len(self._registered_keys | set(super().keys()))


# =============================================================================
# DAEMON IPC CLIENT & AUTO-SPAWN HELPER
# =============================================================================
def try_connect_daemon(command: str, target: str = "") -> dict[str, Any] | None:
    uid = os.getuid()
    sock_path = f"/run/user/{uid}/dusky.sock"
    if not os.path.exists(sock_path):
        sock_path = f"/tmp/dusky_{uid}.sock"

    if not os.path.exists(sock_path):
        return None

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(0.002)  # 2ms ultra-strict connect timeout
        client.connect(sock_path)
        client.settimeout(None)

        payload = {"command": command, "target": target}
        req_bytes = json.dumps(payload).encode("utf-8")
        client.sendall(struct.pack(">I", len(req_bytes)) + req_bytes)

        raw_len = client.recv(4, socket.MSG_WAITALL)
        if not raw_len or len(raw_len) < 4:
            return None

        msg_len = struct.unpack(">I", raw_len)[0]
        resp_bytes = client.recv(msg_len, socket.MSG_WAITALL)
        return json.loads(resp_bytes.decode("utf-8"))
    except (socket.error, socket.timeout, ConnectionRefusedError, struct.error, json.JSONDecodeError):
        return None
    finally:
        client.close()

def auto_spawn_daemon() -> None:
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + str(PROJECT_ROOT / "python") + os.pathsep + env.get("PYTHONPATH", "")
        subprocess.Popen(
            [sys.executable, "-m", "main.daemon"],
            cwd=str(PROJECT_ROOT / "python"),
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL
        )
    except Exception as e:
        logging.error(f"Failed to auto-spawn daemon: {e}")


# =============================================================================
# MAIN CLI ROUTER
# =============================================================================
if __name__ == "__main__":
    help_epilog = """
EXAMPLES:
  1. Launch the TUI normally:
     python main.py hypr.input_tui

  2. Headlessly restore all default values (with a backup first):
     python main.py hypr.input_tui --backup --default

  3. Headlessly change a specific setting (use scope.key if ambiguous):
     python main.py hypr.input_tui --set border_size=3

  4. Generate Markdown documentation for a schema:
     python main.py hypr.input_tui --export-docs > docs.md
    """

    parser = argparse.ArgumentParser(
        description="Dusky TUI Master Router - Advanced Configuration Ecosystem",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=help_epilog
    )

    parser.add_argument(
        "module",
        help=(
            "Path, dot-notation, or relative path to the schema file.\n"
            "(e.g., 'hypr.input_tui' or '~/user_scripts/hypr/input_tui.py')"
        )
    )

    safety_group = parser.add_argument_group("Safety & Backups")
    safety_group.add_argument(
        "--backup",
        action="store_true",
        help="Create a backup of the target config before doing anything."
    )
    safety_group.add_argument(
        "--restore",
        action="store_true",
        help="Restore the target config from the latest backup and exit."
    )

    headless_group = parser.add_mutually_exclusive_group()
    headless_group.add_argument(
        "--default",
        action="store_true",
        help="Headlessly restore all schema items to their default values."
    )
    headless_group.add_argument(
        "--reset-key",
        metavar="KEY",
        type=str,
        help="Headlessly restore a specific key to its default."
    )
    headless_group.add_argument(
        "--set",
        metavar="KEY=VALUE",
        type=str,
        help="Headlessly set a value (format: target_key=new_value)."
    )
    headless_group.add_argument(
        "--export-state",
        action="store_true",
        help="Print the parsed AST state as JSON to stdout and exit."
    )
    headless_group.add_argument(
        "--export-docs",
        action="store_true",
        help="Generate a Markdown documentation file based on the schema and exit."
    )

    parser.add_argument(
        "--log",
        action="store_true",
        help="Enable file logging to ~/Documents/logs/tui/"
    )

    daemon_group = parser.add_argument_group("Daemon & Pre-Warming")
    daemon_group.add_argument(
        "--auto-daemon",
        action="store_true",
        help="Transparently spawn detached background daemon if offline."
    )
    daemon_group.add_argument(
        "--daemon-status",
        action="store_true",
        help="Check status of background pre-warming daemon."
    )
    daemon_group.add_argument(
        "--stop-daemon",
        action="store_true",
        help="Stop running background pre-warming daemon."
    )

    args = parser.parse_args()

    if args.stop_daemon:
        res = try_connect_daemon("stop")
        print("[+] Daemon stop signal sent." if res else "[-] Daemon is not responding (already offline).")
        sys.exit(0)

    if args.daemon_status:
        res = try_connect_daemon("status")
        if res:
            print(f"[+] Daemon ACTIVE: {json.dumps(res, indent=2)}")
        else:
            print("[-] Daemon OFFLINE.")
        sys.exit(0)

    # --- 1. SMART SCHEMA PATH RESOLUTION ---
    target_arg = args.module
    direct_path = Path(target_arg).expanduser().resolve()
    schema_path = None

    if direct_path.exists() and direct_path.is_file():
        schema_path = direct_path

    else:
        clean_arg = target_arg.replace(".", "/").lstrip("/")
        if not clean_arg.endswith(".py"):
            clean_arg += ".py"

        for base_dir in SCHEMA_SEARCH_PATHS:
            potential_path = base_dir / clean_arg

            if potential_path.exists() and potential_path.is_file():
                schema_path = potential_path
                break

    if not schema_path:
        print(f"[-] Schema module '{target_arg}' not found.")
        print("[i] Checked direct path and the following directories:")

        for p in SCHEMA_SEARCH_PATHS:
            print(f"    - {p}")

        sys.exit(1)

    module_name = schema_path.stem
    logger = setup_logging(module_name, args.log)

    spec = importlib.util.spec_from_file_location(module_name, schema_path)

    if spec is None or spec.loader is None:
        print(f"[-] Failed to load schema module: Invalid module spec for '{schema_path}'.")
        sys.exit(1)

    schema_module = importlib.util.module_from_spec(spec)

    safe_module_namespace = f"dusky_schema_{module_name}"
    sys.modules[safe_module_namespace] = schema_module

    spec.loader.exec_module(schema_module)

    try:
        SCHEMA = schema_module.SCHEMA
        TABS = schema_module.TABS
        TARGET_FILE = Path(schema_module.TARGET_FILE).expanduser().resolve()

        THEME_FILE = getattr(schema_module, "THEME_FILE", None)
        APP_TITLE = getattr(schema_module, "APP_TITLE", "Dusky Configurator")
        DEFAULT_MODE = getattr(schema_module, "DEFAULT_MODE", "auto")
        ENABLE_USER_PRESETS = getattr(schema_module, "ENABLE_USER_PRESETS", True)
        USER_PRESETS_TAB = getattr(schema_module, "USER_PRESETS_TAB", None)
        GLOBAL_POPUP = getattr(schema_module, "GLOBAL_POPUP", None)
        TAB_NOTICES = getattr(schema_module, "TAB_NOTICES", None)
        DEFERRED_LOAD = getattr(schema_module, "DEFERRED_LOAD", None)
        REQUIRE_ROOT = getattr(schema_module, "REQUIRE_ROOT", False)
        CUSTOM_VIEWS = getattr(schema_module, "CUSTOM_VIEWS", None)

        ENGINE_TYPE = schema_module.ENGINE_TYPE.lower()

    except AttributeError as e:
        print(f"\n[-] Fatal: Invalid schema file '{schema_path.name}'.")

        if "ENGINE_TYPE" in str(e):
            print("[-] Missing required attribute: 'ENGINE_TYPE'")
            print("[i] You must explicitly define ENGINE_TYPE in your schema.")
            print("[i] Example: ENGINE_TYPE = \"lua\"  (or \"ini\")\n")
        else:
            print(f"[-] Missing required attribute: {e}\n")

        sys.exit(1)

    logger.info(f"Loaded schema: {schema_path} | Target: {TARGET_FILE} | Engine: {ENGINE_TYPE}")

    # Always query daemon IPC on TUI launch for hot pre-warmed state
    daemon_resp = try_connect_daemon("get_schema", target=str(schema_path))
    if not daemon_resp and (args.auto_daemon or getattr(schema_module, "AUTO_DAEMON", False)):
        auto_spawn_daemon()

    # =========================================================================
    # --- 1.5 DYNAMIC PRIVILEGE ESCALATION BLOCK ---
    # =========================================================================
    if REQUIRE_ROOT and os.geteuid() != 0:
        print(f"[*] '{APP_TITLE}' requires root privileges. Escalating...")
        logger.info("Elevating privileges via sudo.")

        preserve_vars = [
            "HOME", "USER",
            "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
            "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "DISPLAY", "TERM", "COLORTERM",
            "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY", "LANG", "LC_ALL", "PATH",
            "PYTHONPATH", "PYTHONPYCACHEPREFIX", "VIRTUAL_ENV", "SUDO_USER", "PKEXEC_UID"
        ]

        env_args = []
        for var in preserve_vars:
            if var in os.environ:
                env_args.append(f"{var}={os.environ[var]}")

        escalated_args = list(sys.argv[1:])
        if target_arg in escalated_args:
            escalated_args[escalated_args.index(target_arg)] = str(schema_path)

        target_cmd = [sys.executable, os.path.realpath(sys.argv[0])] + escalated_args

        has_silent_sudo = False

        if shutil.which("sudo"):
            try:
                if subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=2).returncode == 0:
                    has_silent_sudo = True
            except Exception:
                pass

        if has_silent_sudo:
            cmd = ["sudo", "env"] + env_args + target_cmd
            os.execvp(cmd[0], cmd)

        if shutil.which("sudo"):
            cmd = ["sudo", "env"] + env_args + target_cmd
            os.execvp(cmd[0], cmd)

        elif shutil.which("su"):
            su_cmd_str = " ".join([shlex.quote(arg) for arg in (["env"] + env_args + target_cmd)])
            cmd = ["su", "-c", su_cmd_str]
            os.execvp(cmd[0], cmd)

        else:
            print("[-] Fatal: Root privileges required, but no escalation tool (sudo/su) was found.")
            sys.exit(1)

    # =========================================================================
    # --- 2. MULTI-ENGINE LAZY POOL & ROUTER BLOCK ---
    # =========================================================================
    def _create_engine_instance(e_type: str, config_path: str):
        if e_type == "lua":
            from python.engines.lua import HyprlandLuaEngine
            return HyprlandLuaEngine(config_path=config_path)

        elif e_type == "trackpad":
            from python.engines.trackpad import TrackpadLuaEngine
            return TrackpadLuaEngine(config_path=config_path)

        elif e_type == "monitor":
            from python.engines.monitor_engine import MonitorLuaEngine
            return MonitorLuaEngine(config_path=config_path)

        elif e_type == "ini":
            from python.engines.ini import IniConfigEngine
            return IniConfigEngine(config_path=config_path)

        elif e_type == "bridged_ini":
            from python.engines.bridged_ini import BridgedIniEngine
            return BridgedIniEngine(config_path=config_path)

        elif e_type == "systemd":
            from python.engines.systemd import SystemdEngine
            return SystemdEngine()

        elif e_type == "hyprlang":
            from python.engines.hyprlang import HyprlangEngine
            return HyprlangEngine(config_path=config_path)

        elif e_type == "cmdline":
            from python.engines.cmdline import CmdlineEngine
            return CmdlineEngine(config_path=config_path)

        elif e_type == "systemd_boot":
            from python.engines.systemd_boot import SystemdBootEngine
            return SystemdBootEngine(config_path=config_path)

        elif e_type == "flatdotconfig":
            from python.engines.flatdotconfig import FlatDotConfigEngine
            return FlatDotConfigEngine(config_path=config_path)

        elif e_type == "env":
            from python.engines.environment_variables import ShellEnvEngine
            return ShellEnvEngine(config_path=config_path)

        elif e_type == "shell_fallback":
            from python.engines.shell_fallback import ShellFallbackEngine
            return ShellFallbackEngine(config_path=config_path)

        elif e_type == "waybar":
            from python.engines.waybar_engine import WaybarEngine
            return WaybarEngine(config_path=config_path)

        elif e_type == "network":
            from python.engines.network_manager import NetworkManagerEngine
            return NetworkManagerEngine(config_path=config_path)

        elif e_type == "pkg_throttle":
            from python.engines.pkg_throttle import PkgThrottleEngine
            return PkgThrottleEngine(config_path=config_path)

        elif e_type == "cpu_core":
            from python.engines.cpu_core import CpuCoreEngine
            return CpuCoreEngine(config_path=config_path)

        elif e_type == "fstab":
            from python.engines.fstab import FstabEngine
            return FstabEngine(config_path=config_path)

        elif e_type == "json":
            from python.engines.json_engine import JsonEngine
            return JsonEngine(config_path=config_path)

        elif e_type == "locale_gen":
            from python.engines.locale_gen import LocaleGenEngine
            return LocaleGenEngine(config_path=config_path)

        else:
            print(f"[-] Fatal: Unknown ENGINE_TYPE '{e_type}' specified in schema '{schema_path.name}'.")
            print(
                "[i] Supported engines are: 'lua', 'ini', 'bridged_ini', 'systemd', 'hyprlang', "
                "'trackpad', 'monitor', 'cmdline', 'systemd_boot', 'flatdotconfig', 'env', "
                "'waybar', 'network', 'pkg_throttle', 'cpu_core', 'fstab', 'shell_fallback', 'json', 'locale_gen'"
            )
            sys.exit(1)

    engine_pool = LazyEnginePool(_create_engine_instance)

    default_engine_key = engine_pool.register(ENGINE_TYPE, str(TARGET_FILE))

    for tab_idx, items in SCHEMA.items():
        for item in items:
            if getattr(item, "engine_type_override", None) or getattr(item, "target_file_override", None):
                override_etype = (item.engine_type_override or ENGINE_TYPE).lower()
                override_tfile = (
                    str(Path(item.target_file_override).expanduser().resolve())
                    if item.target_file_override
                    else str(TARGET_FILE)
                )
                engine_pool.register(override_etype, override_tfile)

    # --- 3. PRE-FLIGHT CHECKS (Backups / Restores) ---
    is_headless = any([args.default, args.reset_key, args.set, args.export_state, args.export_docs])

    unique_targets = {TARGET_FILE}

    for items in SCHEMA.values():
        for item in items:
            if getattr(item, "target_file_override", None):
                unique_targets.add(Path(item.target_file_override).expanduser().resolve())

    if args.restore:
        can_restore_all = True

        for t_file in unique_targets:
            if not manage_backup(t_file, "check_restore", logger):
                can_restore_all = False

        if not can_restore_all:
            print("[-] Atomic restore aborted: One or more required backup files are missing.")
            sys.exit(1)

        for t_file in unique_targets:
            manage_backup(t_file, "restore", logger)

        if not is_headless and not args.backup:
            sys.exit(0)

    if args.backup:
        for t_file in unique_targets:
            manage_backup(t_file, "create", logger)

        if not is_headless:
            sys.exit(0)

    # --- 4. HEADLESS OPERATIONS ---
    if is_headless:
        if DEFERRED_LOAD:
            DEFERRED_LOAD()

        for ekey in list(engine_pool):
            engine_pool[ekey].load_state()

        if args.export_state:
            merged_state = {}

            for ekey in list(engine_pool):
                eng = engine_pool[ekey]
                st = eng.cache if hasattr(eng, "cache") else eng.load_state()

                if ekey == default_engine_key:
                    merged_state.update(st)
                else:
                    file_path = Path(ekey[1])
                    path_hash = hashlib.blake2b(str(file_path.resolve()).encode(), digest_size=2).hexdigest()
                    safe_namespace = f"{file_path.parent.name}_{file_path.name}_{path_hash}"

                    for k, v in st.items():
                        merged_state[f"{safe_namespace}::{k}"] = v

            print(json.dumps(merged_state, indent=2))
            sys.exit(0)

        if args.export_docs:
            print(f"# Configuration Reference: {APP_TITLE}\n")

            for tab_idx, items in SCHEMA.items():
                tab_name = TABS[tab_idx] if isinstance(TABS, dict) else TABS[tab_idx]
                print(f"## {tab_name}")

                for item in items:
                    if item.type_ in ("action", "preset", "menu"):
                        continue

                    print(f"### `{item.key}`")
                    print(f"- **Type:** `{item.type_}`")
                    print(f"- **Default:** `{item.default}`")

                    if item.extended_help:
                        print(f"\n> {item.extended_help.replace('**', '')}\n")

                    if item.confirm_message:
                        print(f"\n> **Requires Confirmation:** {item.confirm_message.replace('**', '')}\n")

                    if item.warning_msg:
                        print(f"\n> **Warning:** {item.warning_msg.replace('**', '')}\n")

            sys.exit(0)

        flat_schema = {}

        for items in SCHEMA.values():
            for item in items:
                if item.type_ in ("action", "preset", "menu"):
                    continue

                scoped_key = f"{item.scope}.{item.key}"
                flat_schema[scoped_key] = item

                if item.key in flat_schema:
                    if flat_schema[item.key] is not item:
                        flat_schema[item.key] = None
                else:
                    flat_schema[item.key] = item

        if args.set:
            if "=" not in args.set:
                print("[-] Format error: Use --set key=value")
                sys.exit(1)

            target_key, val_str = args.set.split("=", 1)

            if target_key not in flat_schema:
                print(f"[-] Key '{target_key}' not found in schema.")
                sys.exit(1)

            item = flat_schema[target_key]

            if item is None:
                print(f"[-] Key '{target_key}' is ambiguous across multiple scopes. Please specify using 'scope.{target_key}'.")
                sys.exit(1)

            e_type = (item.engine_type_override or ENGINE_TYPE).lower()
            t_file = (
                str(Path(item.target_file_override).expanduser().resolve())
                if item.target_file_override
                else str(TARGET_FILE)
            )

            target_engine = engine_pool[(e_type, t_file)]
            val_str = item.serialize(val_str)

            logger.info(f"Headless Injection: {target_key} -> {val_str}")

            success, msg, _ = target_engine.write_value(item.key, item.scope, val_str, item_type=item.type_)

            print(f"[{'OK' if success else 'FAIL'}] {msg}")
            sys.exit(0 if success else 1)

        if args.reset_key:
            if args.reset_key not in flat_schema:
                print(f"[-] Key '{args.reset_key}' not found in schema.")
                sys.exit(1)

            item = flat_schema[args.reset_key]

            if item is None:
                print(f"[-] Key '{args.reset_key}' is ambiguous across multiple scopes. Please specify using 'scope.{args.reset_key}'.")
                sys.exit(1)

            e_type = (item.engine_type_override or ENGINE_TYPE).lower()
            t_file = (
                str(Path(item.target_file_override).expanduser().resolve())
                if item.target_file_override
                else str(TARGET_FILE)
            )

            target_engine = engine_pool[(e_type, t_file)]
            val = item.serialize(item.default)

            logger.info(f"Headless Reset Key: {args.reset_key} -> {val}")

            success, msg, _ = target_engine.write_value(item.key, item.scope, val, item_type=item.type_)

            print(f"[{'OK' if success else 'FAIL'}] {msg}")
            sys.exit(0 if success else 1)

        if args.default:
            logger.info("Initiating Full Headless Default Restoration")

            unique_items = {id(item): item for item in flat_schema.values() if item is not None}.values()
            changes_by_engine = {}

            for item in unique_items:
                val = item.serialize(item.default)

                e_type = (item.engine_type_override or ENGINE_TYPE).lower()
                t_file = (
                    str(Path(item.target_file_override).expanduser().resolve())
                    if item.target_file_override
                    else str(TARGET_FILE)
                )

                ekey = (e_type, t_file)

                if ekey not in changes_by_engine:
                    changes_by_engine[ekey] = []

                changes_by_engine[ekey].append((item.key, item.scope, val, item.type_))

            all_success = True

            for ekey, changes in changes_by_engine.items():
                success, msg, _ = engine_pool[ekey].write_batch(changes)

                if success:
                    print(f"[*] Restoration Complete for {ekey[0]} backend. Reset {len(changes)} items successfully.")
                else:
                    success_count, skip_count = 0, 0

                    for key, scope, val, itype in changes:
                        ok, _, _ = engine_pool[ekey].write_value(key, scope, val, item_type=itype)

                        if ok:
                            success_count += 1
                        else:
                            skip_count += 1

                    if skip_count == 0:
                        print(f"[*] Restoration Complete for {ekey[0]} backend via fallback. Reset {success_count} items successfully.")
                    else:
                        print(f"[*] Partial Restoration Complete for {ekey[0]} backend. Reset: {success_count} | Skipped: {skip_count}")
                        all_success = False

            sys.exit(0 if all_success else 1)

    # --- 5. INTERACTIVE TUI EXECUTION ---
    logger.info("Launching TUI")

    # DEFERRED IMPORT: Prevents UI dependencies from crashing the headless CLI 
    from python.frontend.ui import DuskyTUI

    app = DuskyTUI(
        engine_pool=engine_pool,
        default_engine_key=default_engine_key,
        schema=SCHEMA,
        tabs=TABS,
        title=APP_TITLE,
        theme_path=THEME_FILE,
        default_mode=DEFAULT_MODE,
        schema_name=module_name,
        enable_user_presets=ENABLE_USER_PRESETS,
        user_presets_tab=USER_PRESETS_TAB,
        global_popup=GLOBAL_POPUP,
        tab_notices=TAB_NOTICES,
        deferred_load=DEFERRED_LOAD,
        custom_views=CUSTOM_VIEWS
    )

    for engine in list(engine_pool.values()):
        if hasattr(engine, "set_app"):
            engine.set_app(app)

    app.run()
