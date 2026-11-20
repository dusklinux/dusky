#!/usr/bin/env python3
import sys
import os

lazy import argparse
lazy import importlib
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
lazy from python.frontend.core_types import is_trigger_item
lazy from python.frontend.ui import DuskyTUI


def _xdg_dir(env_var: str, fallback_rel: str) -> Path:
    raw = os.environ.get(env_var, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / fallback_rel).resolve()


_PENDING_CHOWN_CLEANUP = None


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

            for xdg_var, default_suffix in (
                ("XDG_CONFIG_HOME", ".config"),
                ("XDG_CACHE_HOME", ".cache"),
                ("XDG_DATA_HOME", ".local/share"),
                ("XDG_STATE_HOME", ".local/state"),
            ):
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

            def _fix_permissions() -> None:
                try:
                    targets = (
                        _xdg_dir("XDG_CONFIG_HOME", ".config") / "dusky",
                        _xdg_dir("XDG_CACHE_HOME", ".cache") / "dusky_tui",
                        _xdg_dir("XDG_STATE_HOME", ".local/state") / "dusky" / "logs",
                        _xdg_dir("XDG_DATA_HOME", ".local/share") / "dusky_backups",
                        Path.home() / "Documents" / "dusky_backups",
                    )
                    for target in targets:
                        if target.exists():
                            _fast_chown_tree(str(target))
                except Exception:
                    pass

            # Armed only after CLI parsing succeeds so --help never walks trees.
            _PENDING_CHOWN_CLEANUP = _fix_permissions

        except KeyError:
            pass


# =============================================================================
# CACHE & IOC SETUP
# =============================================================================
def _setup_cache() -> None:
    if sys.pycache_prefix is not None or sys.dont_write_bytecode:
        return
    try:
        cache_dir = _xdg_dir("XDG_CACHE_HOME", ".cache") / "dusky_tui"
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


def _discover_schema_search_paths() -> tuple[Path, ...]:
    """Expand canonical search paths on demand, followed by XDG and repo fallbacks."""
    candidates: list[Path] = []
    if env_dirs := os.environ.get("DUSKY_SCHEMA_PATH", "").strip():
        for raw in env_dirs.split(os.pathsep):
            if raw.strip():
                candidates.append(Path(raw.strip()).expanduser().resolve())

    for raw in SCHEMA_SEARCH_PATHS:
        candidates.append(Path(raw).expanduser().resolve())

    xdg_cfg = _xdg_dir("XDG_CONFIG_HOME", ".config")
    candidates.extend((
        xdg_cfg / "dusky_schema",
        xdg_cfg / "dusky" / "schemas",
        PROJECT_ROOT / "schemas",
        PROJECT_ROOT.parent,
    ))

    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return tuple(ordered)


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================
def setup_logging(module_name: str, enable_logging: bool) -> logging.Logger:
    """Configures logging. Attaches a NullHandler if disabled to prevent TUI corruption."""
    logger = logging.getLogger("dusky_router")
    logger.setLevel(logging.DEBUG if enable_logging else logging.WARNING)

    if enable_logging:
        log_dir = _xdg_dir("XDG_STATE_HOME", ".local/state") / "dusky" / "logs"
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
    backup_dir = Path.home() / "Documents" / "dusky_backups" / "tui_reset"
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
            try:
                out_f = os.fdopen(fd, "wb")
            except BaseException:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            with out_f, src.open("rb") as in_f:
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
        self._schema_override = None
        self._app = None
        self._owner_thread = get_ident()

    def bind_app(self, app) -> None:
        with self._factory_lock:
            self._app = app
            self._owner_thread = get_ident()
            self._bound_keys.clear()
            for key in tuple(self._engines):
                self._bind_engine(key)

    def detach_app(self) -> None:
        """Prevent in-flight constructors from binding to a closing UI."""
        with self._factory_lock:
            self._app = None

    def register(self, e_type: str, config_path: str) -> tuple[str, str]:
        key = (e_type, config_path)
        with self._factory_lock:
            self._registered_keys[key] = None
        return key

    def __getitem__(self, key: tuple[str, str]):
        with self._factory_lock:
            if key not in self._registered_keys:
                raise KeyError(key)
            engine = self._engines.get(key)
            if engine is None:
                construction_lock = self._construction_locks.get(key)
                if construction_lock is None:
                    construction_lock = self._construction_locks[key] = RLock()
            else:
                construction_lock = None

        if construction_lock is not None:
            # Imports and constructors for unrelated targets can overlap.
            with construction_lock:
                with self._factory_lock:
                    engine = self._engines.get(key)
                if engine is None:
                    engine = self._factory(*key)
                    with self._factory_lock:
                        self._engines[key] = engine

        # Release the factory lock before waiting on the UI.
        app = self._app
        if app is not None:
            with self._factory_lock:
                already_bound = key in self._bound_keys
            if not already_bound:
                if app.is_running and get_ident() != self._owner_thread:
                    try:
                        app.call_from_thread(self._bind_engine, key)
                    except RuntimeError:
                        if self._app is app:
                            raise
                else:
                    self._bind_engine(key)
        return engine

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
        with self._factory_lock:
            self._registered_keys[key] = None
            self._engines[key] = engine
            self._bound_keys.discard(key)

    def __delitem__(self, key) -> None:
        with self._factory_lock:
            del self._registered_keys[key]
            self._engines.pop(key, None)
            self._bound_keys.discard(key)
            self._construction_locks.pop(key, None)

    def __contains__(self, key: object) -> bool:
        with self._factory_lock:
            return key in self._registered_keys

    def get(self, key: tuple[str, str], default=None):
        return self[key] if key in self else default

    def __iter__(self):
        with self._factory_lock:
            return iter(tuple(self._registered_keys))

    def __len__(self):
        with self._factory_lock:
            return len(self._registered_keys)

    def clear(self) -> None:
        with self._factory_lock:
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
        help="Enable file logging to $XDG_STATE_HOME/dusky/logs/",
    )

    args = parser.parse_args()
    if _PENDING_CHOWN_CLEANUP is not None:
        atexit.register(_PENDING_CHOWN_CLEANUP)
    _setup_cache()

    # --- 1. SMART SCHEMA PATH RESOLUTION ---
    target_arg = args.module
    direct_path = Path(target_arg).expanduser().resolve()
    schema_search_paths = _discover_schema_search_paths()
    schema_path = None

    if direct_path.exists() and direct_path.is_file():
        schema_path = direct_path
    else:
        clean_arg = target_arg.removesuffix(".py")
        if "/" not in clean_arg:
            clean_arg = clean_arg.replace(".", "/")
        clean_arg = clean_arg.lstrip("/") + ".py"

        for base_dir in schema_search_paths:
            potential_path = base_dir / clean_arg
            if potential_path.exists() and potential_path.is_file():
                schema_path = potential_path
                break

    if not schema_path:
        print(f"[-] Schema module '{target_arg}' not found.")
        print("[i] Checked direct path and the following directories:")
        for p in schema_search_paths:
            print(f"    - {p}")
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
        if THEME_FILE:
            THEME_FILE = str(Path(THEME_FILE).expanduser().resolve())
        else:
            default_dusky = _xdg_dir("XDG_CONFIG_HOME", ".config") / "matugen" / "generated" / "dusky.json"
            if default_dusky.is_file():
                THEME_FILE = str(default_dusky)

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

        preserve_vars = (
            "HOME", "USER",
            "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
            "XDG_RUNTIME_DIR", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE",
            "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE",
            "TERM", "COLORTERM", "TERMINFO", "TERMINFO_DIRS",
            "DBUS_SESSION_BUS_ADDRESS", "LANG", "LC_ALL", "PATH",
            "PYTHONPATH", "PYTHONPYCACHEPREFIX", "VIRTUAL_ENV",
            "SUDO_USER", "PKEXEC_UID", "DUSKY_SCHEMA_PATH",
        )

        env_args = []
        for var in preserve_vars:
            if var in os.environ:
                env_args.append(f"{var}={os.environ[var]}")

        escalated_args = list(sys.argv[1:])
        skip_val = False
        for arg_idx, token in enumerate(escalated_args):
            if skip_val:
                skip_val = False
                continue
            if token == "--":
                if arg_idx + 1 < len(escalated_args) and escalated_args[arg_idx + 1] == target_arg:
                    escalated_args[arg_idx + 1] = str(schema_path)
                break
            if token in ("--set", "--reset-key"):
                skip_val = True
                continue
            if token.startswith("-"):
                continue
            if token == target_arg:
                escalated_args[arg_idx] = str(schema_path)
            break

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
    # --- 2. DECLARATIVE MULTI-ENGINE LAZY POOL & ROUTER BLOCK ---
    # =========================================================================
    # Maps engine alias -> (module_path, class_name, init_mode)
    # init_mode: "config" | "none" | "items" | "defaults"
    _ENGINE_REGISTRY: dict[str, tuple[str, str, str]] = {
        "neovim":              ("python.engines.neovim",                "NeovimEngine",             "items"),
        "lua":                 ("python.engines.lua",                   "HyprlandLuaEngine",        "config"),
        "autostart":           ("python.engines.autostart_engine",      "AutostartLuaEngine",       "config"),
        "trackpad":            ("python.engines.trackpad",              "TrackpadLuaEngine",        "config"),
        "monitor":             ("python.engines.monitor_engine",        "MonitorLuaEngine",         "config"),
        "ini":                 ("python.engines.ini",                   "IniConfigEngine",          "config"),
        "tlp":                 ("python.engines.tlp",                   "TlpConfigEngine",          "config"),
        "bridged_ini":         ("python.engines.bridged_ini",           "BridgedIniEngine",         "config"),
        "systemd":             ("python.engines.systemd",               "SystemdEngine",            "none"),
        "hyprlang":            ("python.engines.hyprlang",              "HyprlangEngine",           "config"),
        "cmdline":             ("python.engines.cmdline",               "CmdlineEngine",            "config"),
        "systemd_boot":        ("python.engines.systemd_boot",          "SystemdBootEngine",        "config"),
        "flatdotconfig":       ("python.engines.flatdotconfig",         "FlatDotConfigEngine",      "config"),
        "env":                 ("python.engines.environment_variables", "ShellEnvEngine",           "config"),
        "shell_fallback":      ("python.engines.shell_fallback",        "ShellFallbackEngine",      "config"),
        "waybar":              ("python.engines.waybar_engine",         "WaybarEngine",             "config"),
        "network":             ("python.engines.network_manager",       "NetworkManagerEngine",     "config"),
        "pkg_throttle":        ("python.engines.pkg_throttle",          "PkgThrottleEngine",        "config"),
        "cpu_core":            ("python.engines.cpu_core",              "CpuCoreEngine",            "config"),
        "fstab":               ("python.engines.fstab",                 "FstabEngine",              "config"),
        "json":                ("python.engines.json_engine",           "JsonEngine",               "config"),
        "dusky_sites":         ("python.engines.dusky_sites",           "DuskySitesEngine",         "config"),
        "locale_gen":          ("python.engines.locale_gen",            "LocaleGenEngine",          "config"),
        "matugen":             ("python.engines.matugen",               "MatugenEngine",            "config"),
        "matugen_toml":        ("python.engines.matugen",               "MatugenEngine",            "config"),
        "matugen_presets":     ("python.engines.matugen_presets",       "MatugenPresetsEngine",     "config"),
        "matugen_color":       ("python.engines.matugen_presets",       "MatugenPresetsEngine",     "config"),
        "color_presets":       ("python.engines.matugen_presets",       "MatugenPresetsEngine",     "config"),
        "theme_presets":       ("python.engines.matugen_presets",       "MatugenPresetsEngine",     "config"),
        "fontconfig":          ("python.engines.fontconfig",            "FontconfigEngine",         "defaults"),
        "toml":                ("python.engines.toml",                  "TomlEngine",               "config"),
        "toml_engine":         ("python.engines.toml",                  "TomlEngine",               "config"),
        "bluetooth_proximity": ("python.engines.bluetooth_proximity",   "BluetoothProximityEngine", "config"),
        "kokoro":              ("python.engines.kokoro",                "KokoroEngine",             "config"),
        "dusky_kokoro":        ("python.engines.kokoro",                "KokoroEngine",             "config"),
        "systemd_dns":         ("python.engines.dns_systemd",           "SystemdDnsEngine",         "config"),
        "systemd_power":       ("python.engines.systemd_power",         "SystemdPowerEngine",       "config"),
        "systemd_logind":      ("python.engines.systemd_power",         "SystemdPowerEngine",       "config"),
        "power_engine":        ("python.engines.systemd_power",         "SystemdPowerEngine",       "config"),
        "starship":            ("python.engines.starship",              "StarshipEngine",           "config"),
        "hyprlock":            ("python.engines.hyprlock",              "HyprlockEngine",           "config"),
        "ufw":                 ("python.engines.ufw",                   "UfwEngine",                "config"),
        "ufw_firewall":        ("python.engines.ufw",                   "UfwEngine",                "config"),
        "gsettings":           ("python.engines.gsettings",             "GSettingsEngine",          "items"),
        "gsetting":            ("python.engines.gsettings",             "GSettingsEngine",          "items"),
        "dconf":               ("python.engines.gsettings",             "GSettingsEngine",          "items"),
        "hypr_shader":         ("python.engines.hypr_shader",           "HyprShaderEngine",         "config"),
        "shader":              ("python.engines.hypr_shader",           "HyprShaderEngine",         "config"),
        "dusky_shader":        ("python.engines.hypr_shader",           "HyprShaderEngine",         "config"),
    }

    # Tracks live schema items (including prospective deferred discovery rows).
    _active_schema_source = SCHEMA

    def _items_for_engine(e_type: str, config_path: str) -> list:
        source = getattr(engine_pool, "_schema_override", None) or _active_schema_source
        return [
            item
            for tab in source.values()
            for item in tab
            if item.type_ not in ("action", "preset", "menu")
            and (item.engine_type_override or ENGINE_TYPE).lower() == e_type
            and (
                resolve_target(item.target_file_override)
                if item.target_file_override
                else str(TARGET_FILE)
            ) == config_path
        ]

    def _create_engine_instance(e_type: str, config_path: str):
        entry = _ENGINE_REGISTRY.get(e_type)
        if entry is None:
            supported = ", ".join(f"'{k}'" for k in sorted(_ENGINE_REGISTRY))
            raise ValueError(
                f"Unknown ENGINE_TYPE '{e_type}' specified in schema '{schema_path.name}'. "
                f"Supported engines: {supported}"
            )

        mod_path, cls_name, init_mode = entry
        engine_cls = getattr(importlib.import_module(mod_path), cls_name)

        match init_mode:
            case "none":
                return engine_cls()
            case "items":
                return engine_cls(
                    config_path=config_path,
                    items=_items_for_engine(e_type, config_path),
                )
            case "defaults":
                defaults = {
                    item.key: item.default
                    for item in _items_for_engine(e_type, config_path)
                }
                return engine_cls(config_path=config_path, defaults=defaults)
            case _:
                return engine_cls(config_path=config_path)

    engine_pool = LazyEnginePool(_create_engine_instance)
    default_engine_key = engine_pool.register(ENGINE_TYPE, str(TARGET_FILE))

    def _register_schema_engines() -> None:
        for items in SCHEMA.values():
            for item in items:
                if item.engine_type_override or item.target_file_override:
                    override_etype = (item.engine_type_override or ENGINE_TYPE).lower()
                    override_tfile = (
                        resolve_target(item.target_file_override)
                        if item.target_file_override
                        else str(TARGET_FILE)
                    )
                    engine_pool.register(override_etype, override_tfile)

    def _validate_registered_engines() -> None:
        unknown = sorted({e_type for e_type, _path in engine_pool if e_type not in _ENGINE_REGISTRY})
        if unknown:
            supported = ", ".join(f"'{k}'" for k in sorted(_ENGINE_REGISTRY))
            for bad in unknown:
                print(f"[-] Fatal: Unknown ENGINE_TYPE '{bad}' specified in schema '{schema_path.name}'.")
            print(f"[i] Supported engines are: {supported}")
            sys.exit(1)

    _register_schema_engines()
    _validate_registered_engines()

    # --- 3. PRE-FLIGHT CHECKS (Backups / Restores) ---
    is_headless = (
        args.default
        or args.reset_key is not None
        or args.set is not None
        or args.export_state
        or args.export_docs
    )

    _GSETTINGS_ALIASES = frozenset({"gsettings", "gsetting", "dconf"})

    def _reject_gsettings_file_backups() -> None:
        if (args.backup or args.restore) and any(e_type in _GSETTINGS_ALIASES for e_type, _ in engine_pool):
            print(
                "[-] File backups cannot restore a live GSettings backend. "
                "Use saved TUI presets or dconf dump/load.",
                file=sys.stderr,
            )
            sys.exit(1)

    _reject_gsettings_file_backups()

    # Run deferred discovery before backup/restore or headless commands so
    # dynamically discovered target files and engines are included.
    if DEFERRED_LOAD and (is_headless or args.backup or args.restore):
        deferred_result = DEFERRED_LOAD()
        if isinstance(deferred_result, tuple) and len(deferred_result) >= 2:
            _, discovered_items = deferred_result[:2]
            if discovered_items:
                SCHEMA.update(discovered_items)
        _register_schema_engines()
        _validate_registered_engines()
        _reject_gsettings_file_backups()

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
        try:
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
                tab_names = dict(enumerate(TABS)) if isinstance(TABS, (list, tuple)) else dict(TABS)

                for tab_idx, items in SCHEMA.items():
                    tab_name = tab_names.get(tab_idx, f"Tab {tab_idx}")
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

                if e_type in _GSETTINGS_ALIASES:
                    wire_val = val_str
                else:
                    try:
                        parsed_val = item.parse_user_input(val_str)
                    except ValueError as exc:
                        print(f"[-] Invalid value for '{matched_key}': {exc}")
                        sys.exit(1)
                    wire_val = item.serialize(parsed_val)

                target_engine = engine_pool[(e_type, t_file)]
                target_engine.load_state()

                logger.info(f"Headless Injection: {matched_key} -> {wire_val}")

                success, msg, _ = target_engine.write_value(
                    item.key, item.scope, wire_val, item_type=item.type_
                )

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

                if is_trigger_item(item):
                    print(f"[-] Key '{args.reset_key}' cannot be reset.")
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

                unique_items = (
                    item
                    for items in SCHEMA.values()
                    for item in items
                    if item.type_ not in ("action", "preset", "menu")
                    and not item.read_only
                    and not is_trigger_item(item)
                )
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
        finally:
            for eng in engine_pool.initialized_values():
                if callable(shutdown := getattr(eng, "shutdown", None)):
                    shutdown()


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
