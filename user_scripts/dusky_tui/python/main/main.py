#!/usr/bin/env python3
import sys
import os

lazy import argparse
lazy import importlib.util
lazy import json
lazy import shutil
lazy import logging
lazy import hashlib
lazy import pwd
lazy import shlex
lazy import atexit
lazy import tempfile
lazy import re
lazy from datetime import datetime
from pathlib import Path
from functools import lru_cache
from collections.abc import MutableMapping
lazy from threading import get_ident, RLock
lazy from python.frontend.ui import DuskyTUI


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
        except (KeyError, ValueError):
            pass

    if _real_uid is not None and _real_uid != 0 and _real_gid is not None:
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

            # Root can access these directories already. Repair ownership on
            # exit instead of scanning the entire backup/cache tree at startup.
            atexit.register(_fix_permissions)

        except KeyError:
            pass


# =============================================================================
# CACHE & IOC SETUP
# =============================================================================
def _setup_cache() -> None:
    if sys.pycache_prefix is not None or sys.dont_write_bytecode:
        return
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


PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SCHEMA_SEARCH_PATHS = (
    "~/user_scripts",
    "~/.config/dusky_schema",
    "~/Documents/schemas",
)


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
        handler = fh

        print(f"[*] Logging enabled: {log_file}", file=sys.stderr)

    else:
        handler = logging.NullHandler()
        logger.addHandler(handler)

    for name in ("dusky_network_engine", "python.frontend.ui"):
        related = logging.getLogger(name)
        related.setLevel(logging.DEBUG if enable_logging else logging.WARNING)
        related.addHandler(handler)

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

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / f"{stem}.{timestamp}.bak"
    latest_link = backup_dir / f"{stem}.latest.bak"

    def atomic_copy(src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out_f, src.open("rb") as in_f:
                os.fchmod(out_f.fileno(), os.fstat(in_f.fileno()).st_mode & 0o7777)
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
                print(f"[-] Missing backup for: {resolved.name}", file=sys.stderr)
                return False
            return True

        case "restore":
            if not latest_link.exists():
                return False
            actual = latest_link.resolve(strict=True)
            atomic_copy(actual, resolved)
            print(f"[+] Restored: {resolved} from {actual.name}", file=sys.stderr)
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
            print(f"[+] Backup created: {backup_path.name}", file=sys.stderr)
            logger.info("Created backup for %s at %s", resolved, backup_path)
            return True

        case _:
            return False


# =============================================================================
# LAZY ENGINE POOL FACTORY
# =============================================================================
class LazyEnginePool(MutableMapping):
    """Registered engine mapping; construct each engine once on active lookup."""
    def __init__(self, factory_func):
        self._factory = factory_func
        self._registered_keys: dict[tuple[str, str], None] = {}
        self._engines = {}
        self._bound_keys = set()
        self._factory_lock = RLock()
        self._construction_locks = {}
        self._app = None
        self._owner_thread = get_ident()

    def bind_app(self, app) -> None:
        self._app = app
        self._owner_thread = get_ident()
        self._bound_keys.clear()
        for key in tuple(self._engines):
            self._bind_engine(key)

    def detach_app(self) -> None:
        """Prevent in-flight constructors from binding to a closing UI."""
        self._app = None

    def register(self, e_type: str, config_path: str) -> tuple[str, str]:
        key = (e_type, config_path)
        self._registered_keys[key] = None
        return key

    def __getitem__(self, key: tuple[str, str]):
        if key not in self._registered_keys:
            raise KeyError(key)
        if key not in self._engines:
            with self._factory_lock:
                if key not in self._construction_locks:
                    self._construction_locks[key] = RLock()
                construction_lock = self._construction_locks[key]
            # Imports and constructors for unrelated targets can overlap.
            # Only callers requesting the same engine share a construction lock.
            with construction_lock:
                if key not in self._engines:
                    engine = self._factory(*key)
                    with self._factory_lock:
                        self._engines[key] = engine
        # Release the factory lock before waiting on the UI. A simultaneous
        # UI lookup can bind the published instance itself without deadlocking.
        # Slow imports/constructors stay on the requesting worker thread.
        app = self._app
        if app is not None and key not in self._bound_keys:
            if app.is_running and get_ident() != self._owner_thread:
                try:
                    app.call_from_thread(self._bind_engine, key)
                except RuntimeError:
                    # Detachment may race with Textual closing its event loop.
                    # Preserve binding errors while this app still owns the pool.
                    if self._app is app:
                        raise
            else:
                self._bind_engine(key)
        return self._engines[key]

    def _bind_engine(self, key) -> None:
        with self._factory_lock:
            if self._app is None or key in self._bound_keys:
                return
            self._bound_keys.add(key)
            try:
                if hasattr(engine := self._engines[key], "set_app"):
                    engine.set_app(self._app)
            except BaseException:
                self._bound_keys.discard(key)
                raise

    def __setitem__(self, key, engine) -> None:
        self._registered_keys[key] = None
        self._engines[key] = engine
        self._bound_keys.discard(key)

    def __delitem__(self, key) -> None:
        del self._registered_keys[key]
        self._engines.pop(key, None)
        self._bound_keys.discard(key)

    def __contains__(self, key: object) -> bool:
        return key in self._registered_keys

    def get(self, key: tuple[str, str], default=None):
        # Missing registrations use the default; constructor/binding errors
        # for registered engines must reach the caller rather than be masked.
        return self[key] if key in self._registered_keys else default

    def __iter__(self):
        return iter(self._registered_keys)

    def __len__(self):
        return len(self._registered_keys)

    def clear(self) -> None:
        self._registered_keys.clear()
        self._engines.clear()
        self._bound_keys.clear()
        self._construction_locks.clear()

    def initialized_values(self):
        """Inspect or shut down engines without constructing unused backends."""
        with self._factory_lock:
            return tuple(self._engines.values())

    def initialized_items(self):
        """Snapshot published engines without constructing unused backends."""
        with self._factory_lock:
            return tuple(self._engines.items())


@lru_cache(maxsize=1024)
def resolve_target(path: str) -> str:
    return str(Path(path).expanduser().resolve())





# =============================================================================
# MAIN CLI ROUTER
# =============================================================================
if __name__ == "__main__":
    help_epilog = """
EXAMPLES:
  1. Launch the TUI normally:
     python3 main.py hypr.input_tui

  2. Headlessly restore all default values (with a backup first):
     python3 main.py hypr.input_tui --backup --default

  3. Headlessly change a specific setting (use scope.key if ambiguous):
     python3 main.py hypr.input_tui --set border_size=3

  4. Generate Markdown documentation for a schema:
     python3 main.py hypr.input_tui --export-docs > docs.md
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

    args = parser.parse_args()
    _setup_cache()

    # --- 1. SMART SCHEMA PATH RESOLUTION ---
    target_arg = args.module
    direct_path = Path(target_arg).expanduser().resolve()
    schema_path = None

    if direct_path.exists() and direct_path.is_file():
        schema_path = direct_path

    else:
        clean_arg = target_arg.removesuffix(".py")
        if "/" not in clean_arg:
            clean_arg = clean_arg.replace(".", "/")
        clean_arg = clean_arg.lstrip("/") + ".py"

        for base_dir in SCHEMA_SEARCH_PATHS:
            potential_path = Path(base_dir).expanduser().resolve() / clean_arg

            if potential_path.exists() and potential_path.is_file():
                schema_path = potential_path
                break

    if not schema_path:
        print(f"[-] Schema module '{target_arg}' not found.")
        print("[i] Checked direct path and the following directories:")

        for p in SCHEMA_SEARCH_PATHS:
            print(f"    - {Path(p).expanduser().resolve()}")

        sys.exit(1)

    module_name = schema_path.stem
    logger = setup_logging(module_name, args.log)

    safe_module_namespace = f"dusky_schema_{module_name}"
    spec = importlib.util.spec_from_file_location(safe_module_namespace, schema_path)

    if spec is None or spec.loader is None:
        print(f"[-] Failed to load schema module: Invalid module spec for '{schema_path}'.")
        sys.exit(1)

    schema_module = importlib.util.module_from_spec(spec)

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
        HIDE_MISSING_ITEMS = getattr(schema_module, "HIDE_MISSING_ITEMS", False)
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



    # =========================================================================
    # --- 1.5 DYNAMIC PRIVILEGE ESCALATION BLOCK ---
    # =========================================================================
    if REQUIRE_ROOT and os.geteuid() != 0:
        print(f"[*] '{APP_TITLE}' requires root privileges. Escalating...", file=sys.stderr)
        logger.info("Elevating privileges via sudo.")

        preserve_vars = [
            "HOME", "USER",
            "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
            "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "TERM", "COLORTERM",
            "DBUS_SESSION_BUS_ADDRESS", "LANG", "LC_ALL", "PATH",
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
        if e_type == "neovim":
            from python.engines.neovim import NeovimEngine
            items = [
                item for tab in SCHEMA.values() for item in tab
                if item.type_ not in ("action", "preset", "menu")
                and (item.engine_type_override or ENGINE_TYPE).lower() == e_type
                and (resolve_target(item.target_file_override) if item.target_file_override
                     else str(TARGET_FILE)) == config_path
            ]
            return NeovimEngine(config_path=config_path, items=items)

        elif e_type == "lua":
            from python.engines.lua import HyprlandLuaEngine
            return HyprlandLuaEngine(config_path=config_path)

        elif e_type == "autostart":
            from python.engines.autostart_engine import AutostartLuaEngine
            return AutostartLuaEngine(config_path=config_path)

        elif e_type == "trackpad":
            from python.engines.trackpad import TrackpadLuaEngine
            return TrackpadLuaEngine(config_path=config_path)

        elif e_type == "monitor":
            from python.engines.monitor_engine import MonitorLuaEngine
            return MonitorLuaEngine(config_path=config_path)

        elif e_type == "ini":
            from python.engines.ini import IniConfigEngine
            return IniConfigEngine(config_path=config_path)

        elif e_type == "tlp":
            from python.engines.tlp import TlpConfigEngine
            return TlpConfigEngine(config_path=config_path)

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

        elif e_type == "dusky_sites":
            from python.engines.dusky_sites import DuskySitesEngine
            return DuskySitesEngine(config_path=config_path)

        elif e_type == "locale_gen":
            from python.engines.locale_gen import LocaleGenEngine
            return LocaleGenEngine(config_path=config_path)

        elif e_type in ("matugen", "matugen_toml"):
            from python.engines.matugen import MatugenEngine
            return MatugenEngine(config_path=config_path)

        elif e_type in ("matugen_presets", "matugen_color", "color_presets", "theme_presets"):
            from python.engines.matugen_presets import MatugenPresetsEngine
            return MatugenPresetsEngine(config_path=config_path)

        elif e_type == "fontconfig":
            from python.engines.fontconfig import FontconfigEngine
            defaults = {
                item.key: item.default
                for items in SCHEMA.values() for item in items
                if item.type_ not in ("action", "preset", "menu")
                and (item.engine_type_override or ENGINE_TYPE).lower() == e_type
                and (resolve_target(item.target_file_override) if item.target_file_override
                     else str(TARGET_FILE)) == config_path
            }
            return FontconfigEngine(config_path=config_path, defaults=defaults)

        elif e_type in ("toml", "toml_engine"):
            from python.engines.toml import TomlEngine
            return TomlEngine(config_path=config_path)

        elif e_type in ("kokoro", "dusky_kokoro"):
            from python.engines.kokoro import KokoroEngine
            return KokoroEngine(config_path=config_path)

        elif e_type == "systemd_dns":
            from python.engines.dns_systemd import SystemdDnsEngine
            return SystemdDnsEngine(config_path=config_path)

        elif e_type in ("systemd_power", "systemd_logind", "power_engine"):
            from python.engines.systemd_power import SystemdPowerEngine
            return SystemdPowerEngine(config_path=config_path)

        elif e_type == "starship":
            from python.engines.starship import StarshipEngine
            return StarshipEngine(config_path=config_path)

        elif e_type == "hyprlock":
            from python.engines.hyprlock import HyprlockEngine
            return HyprlockEngine(config_path=config_path)

        elif e_type in ("ufw", "ufw_firewall"):
            from python.engines.ufw import UfwEngine
            return UfwEngine(config_path=config_path)

        else:
            print(f"[-] Fatal: Unknown ENGINE_TYPE '{e_type}' specified in schema '{schema_path.name}'.")
            print(
                "[i] Supported engines are: 'lua', 'ini', 'tlp', 'bridged_ini', 'systemd', 'systemd_dns', 'systemd_power', 'hyprlang', "
                "'trackpad', 'monitor', 'cmdline', 'systemd_boot', 'flatdotconfig', 'env', "
                "'waybar', 'network', 'pkg_throttle', 'cpu_core', 'fstab', 'shell_fallback', 'json', "
                "'dusky_sites', 'locale_gen', 'matugen', 'fontconfig', 'toml', 'kokoro', 'starship', 'hyprlock', 'ufw'"
            )
            sys.exit(1)

    engine_pool = LazyEnginePool(_create_engine_instance)

    default_engine_key = engine_pool.register(ENGINE_TYPE, str(TARGET_FILE))

    for tab_idx, items in SCHEMA.items():
        for item in items:
            if getattr(item, "engine_type_override", None) or getattr(item, "target_file_override", None):
                override_etype = (item.engine_type_override or ENGINE_TYPE).lower()
                override_tfile = (
                    resolve_target(item.target_file_override)
                    if item.target_file_override
                    else str(TARGET_FILE)
                )
                engine_pool.register(override_etype, override_tfile)

    # --- 3. PRE-FLIGHT CHECKS (Backups / Restores) ---
    is_headless = (
        args.default or args.reset_key is not None or args.set is not None
        or args.export_state or args.export_docs
    )

    unique_targets = {TARGET_FILE}

    if args.restore or args.backup:
        for items in SCHEMA.values():
            for item in items:
                if item.target_file_override:
                    unique_targets.add(Path(resolve_target(item.target_file_override)))

    if args.restore:
        can_restore_all = True

        for t_file in unique_targets:
            if not manage_backup(t_file, "check_restore", logger):
                can_restore_all = False

        if not can_restore_all:
            print("[-] Restore aborted: One or more required backup files are missing.")
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
            deferred_result = DEFERRED_LOAD()
            if isinstance(deferred_result, tuple) and len(deferred_result) >= 2:
                _, discovered_items = deferred_result[:2]
                if discovered_items:
                    SCHEMA.update(discovered_items)
            for items in SCHEMA.values():
                for item in items:
                    engine_pool.register(
                        (item.engine_type_override or ENGINE_TYPE).lower(),
                        resolve_target(item.target_file_override) if item.target_file_override else str(TARGET_FILE),
                    )

        if args.export_state:
            merged_state = {}

            for ekey in list(engine_pool):
                eng = engine_pool[ekey]
                st = eng.load_state()

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
                tab_name = TABS[tab_idx]
                print(f"## {tab_name}")

                for item in items:
                    if item.type_ in ("action", "preset", "menu"):
                        continue

                    print(f"### `{item.key}`")
                    print(f"- **Type:** `{item.type_}`")
                    print(f"- **Default:** `{item.default}`")
                    if item.read_only:
                        print("- **Read only:** yes")

                    if item.extended_help:
                        print(f"\n> {item.extended_help.replace('**', '')}\n")

                    if item.confirm_message:
                        print(f"\n> **Requires Confirmation:** {item.confirm_message.replace('**', '')}\n")

                    if item.warning_msg:
                        print(f"\n> **Warning:** {item.warning_msg.replace('**', '')}\n")

            sys.exit(0)

        flat_schema = {}

        def setting_identity(item):
            return (
                item.scope, item.key,
                (item.engine_type_override or ENGINE_TYPE).lower(),
                resolve_target(item.target_file_override) if item.target_file_override else str(TARGET_FILE),
            )

        for items in SCHEMA.values():
            for item in items:
                if item.type_ in ("action", "preset", "menu"):
                    continue

                scoped_key = f"{item.scope}.{item.key}"
                for lookup_key in (scoped_key, item.key):
                    if lookup_key not in flat_schema:
                        flat_schema[lookup_key] = item
                    elif (previous := flat_schema[lookup_key]) is not None:
                        if setting_identity(previous) != setting_identity(item):
                            flat_schema[lookup_key] = None

        if args.set is not None:
            if "=" not in args.set:
                print("[-] Format error: Use --set key=value")
                sys.exit(1)

            # Match the longest schema key, including ambiguous entries, before
            # splitting the value. Otherwise an ambiguous key containing '='
            # could silently select a shorter, different setting.
            matched_key = None
            val_str = None
            
            sorted_keys = sorted(flat_schema, key=len, reverse=True)
            for k in sorted_keys:
                prefix = f"{k}="
                if args.set.startswith(prefix):
                    matched_key = k
                    val_str = args.set[len(prefix):]
                    break

            if not matched_key:
                target_key, val_str = args.set.split("=", 1)
                if target_key not in flat_schema:
                    print(f"[-] Key '{target_key}' not found in schema.")
                    sys.exit(1)
                matched_key = target_key

            item = flat_schema[matched_key]

            if item is None:
                print(f"[-] Key '{matched_key}' is ambiguous across scopes or target files. Use an unambiguous schema key.")
                sys.exit(1)

            if item.read_only:
                print(f"[-] Key '{matched_key}' is read only.")
                sys.exit(1)

            e_type = (item.engine_type_override or ENGINE_TYPE).lower()
            t_file = (
                resolve_target(item.target_file_override)
                if item.target_file_override
                else str(TARGET_FILE)
            )

            target_engine = engine_pool[(e_type, t_file)]
            target_engine.load_state()
            val_str = item.serialize(val_str)

            logger.info(f"Headless Injection: {matched_key} -> {val_str}")

            success, msg, _ = target_engine.write_value(item.key, item.scope, val_str, item_type=item.type_)

            print(f"[{'OK' if success else 'FAIL'}] {msg}")
            sys.exit(0 if success else 1)

        if args.reset_key is not None:
            if args.reset_key not in flat_schema:
                print(f"[-] Key '{args.reset_key}' not found in schema.")
                sys.exit(1)

            item = flat_schema[args.reset_key]

            if item is None:
                print(f"[-] Key '{args.reset_key}' is ambiguous across scopes or target files. Use an unambiguous schema key.")
                sys.exit(1)

            if item.read_only:
                print(f"[-] Key '{args.reset_key}' is read only.")
                sys.exit(1)

            e_type = (item.engine_type_override or ENGINE_TYPE).lower()
            t_file = (
                resolve_target(item.target_file_override)
                if item.target_file_override
                else str(TARGET_FILE)
            )

            target_engine = engine_pool[(e_type, t_file)]
            target_engine.load_state()
            val = item.serialize(item.default)

            logger.info(f"Headless Reset Key: {args.reset_key} -> {val}")

            success, msg, _ = target_engine.write_value(item.key, item.scope, val, item_type=item.type_)

            print(f"[{'OK' if success else 'FAIL'}] {msg}")
            sys.exit(0 if success else 1)

        if args.default:
            logger.info("Initiating Full Headless Default Restoration")

            unique_items = (item for items in SCHEMA.values() for item in items if item.type_ not in ("action", "preset", "menu") and not item.read_only)
            changes_by_engine = {}

            for item in unique_items:
                val = item.serialize(item.default)

                e_type = (item.engine_type_override or ENGINE_TYPE).lower()
                t_file = (
                    resolve_target(item.target_file_override)
                    if item.target_file_override
                    else str(TARGET_FILE)
                )

                ekey = (e_type, t_file)

                if ekey not in changes_by_engine:
                    changes_by_engine[ekey] = {}

                changes_by_engine[ekey][(item.key, item.scope)] = (item.key, item.scope, val, item.type_)

            all_success = True

            for ekey, indexed_changes in changes_by_engine.items():
                changes = list(indexed_changes.values())
                engine = engine_pool[ekey]
                engine.load_state()
                if hasattr(engine, "write_batch_results"):
                    results = engine.write_batch_results(changes)
                    failures = []
                    for key, scope, _value, _kind in changes:
                        result = results.get((key, scope))
                        if result is None:
                            failures.append(f"Missing write result for {scope}.{key}")
                        elif not result.ok:
                            failures.append(result.message)
                    if failures:
                        all_success = False
                        print(f"[-] {ekey[0]} reset: {len(changes) - len(failures)} applied; {len(failures)} failed. First error: {failures[0]}")
                    else:
                        print(f"[*] Restoration Complete for {ekey[0]} backend. Reset {len(changes)} items successfully.")
                    continue
                success, msg, _ = engine.write_batch(changes)

                if success:
                    print(f"[*] Restoration Complete for {ekey[0]} backend. Reset {len(changes)} items successfully.")
                else:
                    # A failed batch may already have applied some writes.
                    # Replaying it can duplicate side effects or conceal errors.
                    print(f"[-] Default restoration failed for {ekey[0]} backend: {msg}")
                    all_success = False

            sys.exit(0 if all_success else 1)

    # --- 5. INTERACTIVE TUI EXECUTION ---
    logger.info("Launching TUI")

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
        hide_missing_items=HIDE_MISSING_ITEMS,
        custom_views=CUSTOM_VIEWS
    )

    engine_pool.bind_app(app)

    app.run()
