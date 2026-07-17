#!/usr/bin/env python3
import sys
sys.dont_write_bytecode = True
import os
import argparse
import importlib.util
import json
import shutil
import logging
import hashlib
import pwd
import subprocess
import shlex
from datetime import datetime
from pathlib import Path

# =============================================================================
# REAL-USER ENVIRONMENT RECONSTRUCTION (SUDO/PKEXEC SAFETY)
# =============================================================================
# If a user launches the app natively using `sudo main.py`, Path.home() resolves to /root.
# We must intercept this before ANY path resolution happens to restore their genuine $HOME
# so that the schemas, backups, and presets always point to the actual user's files.
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
        except KeyError: pass
    elif _pkexec_uid:
        try:
            _pw = pwd.getpwuid(int(_pkexec_uid))
            _real_uid = _pw.pw_uid
            _real_gid = _pw.pw_gid
        except Exception: pass
        
    if _real_uid and _real_gid:
        try:
            _pw = pwd.getpwuid(_real_uid)
            os.environ["HOME"] = _pw.pw_dir
            os.environ["USER"] = _pw.pw_name
            
            # Extreme Bulletproofing: Guarantee XDG base directories point to the real user.
            # Fixes edge cases where sudo hijacked the XDG tree to /root/.config
            for xdg_var, default_suffix in [
                ("XDG_CONFIG_HOME", ".config"),
                ("XDG_CACHE_HOME", ".cache"),
                ("XDG_DATA_HOME", ".local/share"),
                ("XDG_STATE_HOME", ".local/state")
            ]:
                if xdg_var not in os.environ or os.environ[xdg_var].startswith("/root"):
                    os.environ[xdg_var] = os.path.join(_pw.pw_dir, default_suffix)

            # Proactively fix permissions of any user folders created by root
            def _fix_permissions():
                try:
                    for suffix_dir in [".config/dusky", ".cache/dusky_tui", "Documents/logs/tui", "Documents/dusky_backups/tui_reset"]:
                        target_p = Path(_pw.pw_dir) / suffix_dir
                        if target_p.exists():
                            for root_dir, dirs, files in os.walk(target_p):
                                for d in dirs:
                                    p = Path(root_dir) / d
                                    if p.stat().st_uid == 0:
                                        os.chown(p, _real_uid, _real_gid)
                                for f in files:
                                    p = Path(root_dir) / f
                                    if p.stat().st_uid == 0:
                                        os.chown(p, _real_uid, _real_gid)
                            if target_p.stat().st_uid == 0:
                                os.chown(target_p, _real_uid, _real_gid)
                except Exception:
                    pass

            # Fix permissions at startup and register it at exit
            _fix_permissions()
            import atexit
            atexit.register(_fix_permissions)

            # Monkey-patch Path.mkdir to enforce correct ownership for newly created directories
            import pathlib
            _orig_mkdir = pathlib.Path.mkdir
            def _safe_mkdir(self, mode=0o777, parents=False, exist_ok=False):
                _orig_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)
                try:
                    resolved_p = self.resolve()
                    home_p = Path(_pw.pw_dir).resolve()
                    if resolved_p.is_relative_to(home_p):
                        os.chown(resolved_p, _real_uid, _real_gid)
                        if parents:
                            curr = resolved_p.parent
                            while curr != curr.parent and curr.is_relative_to(home_p):
                                try:
                                    curr_stat = curr.stat()
                                    if curr_stat.st_uid == _real_uid:
                                        break
                                    os.chown(curr, _real_uid, _real_gid)
                                except Exception:
                                    break
                                curr = curr.parent
                except Exception:
                    pass
            pathlib.Path.mkdir = _safe_mkdir

        except KeyError: pass

# =============================================================================
# CACHE & IOC SETUP
# =============================================================================
def _setup_cache() -> None:
    try:
        xdg_cache_env = os.environ.get("XDG_CACHE_HOME", "").strip()
        xdg_cache = Path(xdg_cache_env).expanduser().resolve() if xdg_cache_env else Path.home() / ".cache"
        cache_dir = xdg_cache / "dusky_tui"
        cache_dir.mkdir(parents=True, exist_ok=True)
        sys.pycache_prefix = str(cache_dir)
    except OSError:
        pass

_setup_cache()

# Ensure we can import the core modules
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Notice: We DO NOT import the engines or UI here. They are imported dynamically
# in the router block below to prevent crashing if a dependency is missing
# for an engine you aren't currently using, or if the CLI is running headlessly.

# =============================================================================
# SCHEMA SEARCH PATHS
# Expand this list in the future to allow loading schemas from new locations.
# =============================================================================
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
        fh.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s'))
        logger.addHandler(fh)
        print(f"[*] Logging enabled: {log_file}")
    else:
        logger.addHandler(logging.NullHandler())
    
    return logger

def manage_backup(target_file: Path, action: str, logger: logging.Logger) -> bool:
    """Handles creating and restoring backups across multi-file ecosystems."""
    backup_dir = Path("~/Documents/dusky_backups/tui_reset/").expanduser()
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Prevent cross-engine collisions in the backup folder utilizing a cryptographically secure path hash.
    path_hash = hashlib.md5(str(target_file.resolve()).encode()).hexdigest()[:6]
    safe_prefix = f"{target_file.parent.name}_{path_hash}_" if target_file.parent.name else f"{path_hash}_"
    
    backup_path = backup_dir / f"{safe_prefix}{target_file.name}.{timestamp}.bak"
    latest_link = backup_dir / f"{safe_prefix}{target_file.name}.latest.bak"

    if action == "check_restore":
        if not latest_link.exists():
            print(f"[-] Missing backup for: {target_file.name} (Cannot perform atomic restore)")
            return False
        return True

    if action == "create":
        if not target_file.exists():
            logger.warning(f"Cannot backup, target does not exist: {target_file}")
            return False
        
        shutil.copy2(target_file, backup_path)
        
        latest_link.unlink(missing_ok=True)
        latest_link.symlink_to(backup_path.name)
        
        logger.info(f"Backup created at: {backup_path}")
        print(f"[+] Backup created: {backup_path}")
        return True

    elif action == "restore":
        if not latest_link.exists():
            print(f"[-] No backup found to restore for: {target_file.name}")
            return False
        
        shutil.copy2(latest_link, target_file)
        logger.info(f"Restored from backup: {latest_link}")
        print(f"[+] Successfully restored configuration: {target_file.name}")
        return True
    
    return False

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
        help="Path, dot-notation, or relative path to the schema file.\n(e.g., 'hypr.input_tui' or '~/user_scripts/hypr/input_tui.py')"
    )
    
    safety_group = parser.add_argument_group("Safety & Backups")
    safety_group.add_argument("--backup", action="store_true", help="Create a backup of the target config before doing anything.")
    safety_group.add_argument("--restore", action="store_true", help="Restore the target config from the latest backup and exit.")
    
    headless_group = parser.add_mutually_exclusive_group()
    headless_group.add_argument("--default", action="store_true", help="Headlessly restore all schema items to their default values.")
    headless_group.add_argument("--reset-key", metavar="KEY", type=str, help="Headlessly restore a specific key to its default.")
    headless_group.add_argument("--set", metavar="KEY=VALUE", type=str, help="Headlessly set a value (format: target_key=new_value).")
    headless_group.add_argument("--export-state", action="store_true", help="Print the parsed AST state as JSON to stdout and exit.")
    headless_group.add_argument("--export-docs", action="store_true", help="Generate a Markdown documentation file based on the schema and exit.")

    parser.add_argument("--log", action="store_true", help="Enable file logging to ~/Documents/logs/tui/")

    args = parser.parse_args()

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
    
    # Prefix namespace mapping to strictly prevent silent standard library clobbering
    safe_module_namespace = f"dusky_schema_{module_name}"
    sys.modules[safe_module_namespace] = schema_module
    
    spec.loader.exec_module(schema_module)

    # Extract configuration variables
    try:
        SCHEMA = schema_module.SCHEMA
        TABS = schema_module.TABS
        TARGET_FILE = Path(schema_module.TARGET_FILE).expanduser().resolve()
        
        # Optional attributes / User Preset Hooks
        THEME_FILE = getattr(schema_module, "THEME_FILE", None)
        APP_TITLE = getattr(schema_module, "APP_TITLE", "Dusky Configurator")
        DEFAULT_MODE = getattr(schema_module, "DEFAULT_MODE", "auto")
        ENABLE_USER_PRESETS = getattr(schema_module, "ENABLE_USER_PRESETS", True)
        USER_PRESETS_TAB = getattr(schema_module, "USER_PRESETS_TAB", None)
        GLOBAL_POPUP = getattr(schema_module, "GLOBAL_POPUP", None)
        TAB_NOTICES = getattr(schema_module, "TAB_NOTICES", None) 
        DEFERRED_LOAD = getattr(schema_module, "DEFERRED_LOAD", None)
        
        REQUIRE_ROOT = getattr(schema_module, "REQUIRE_ROOT", False)
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
        print(f"[*] '{APP_TITLE}' requires root privileges. Escalating...")
        logger.info("Elevating privileges via sudo.")
        
        # Arch Linux + Wayland requires explicit preservation of environment to maintain GUI/Audio/Clipboard hooks,
        # as well as the active python PATH execution space (crucial if running in a venv).
        preserve_vars = [
            "HOME", "USER", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
            "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "DISPLAY", "TERM", "COLORTERM", 
            "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY", "LANG", "LC_ALL", "PATH"
        ]
        
        env_args = []
        for var in preserve_vars:
            if var in os.environ:
                env_args.append(f"{var}={os.environ[var]}")

        # sys.executable securely hardlinks the exact Python binary, solving the virtual-env scrubbing issue.
        # os.path.realpath ensures symlinked wrappers resolve securely.
        
        # CRITICAL FIX: sudo configs may scrub the Current Working Directory (CWD).
        # We must rewrite the target argument to its absolute resolved path before escalating,
        # otherwise the root process will look in /root/ and fail to find the schema.
        escalated_args = list(sys.argv[1:])
        if target_arg in escalated_args:
            escalated_args[escalated_args.index(target_arg)] = str(schema_path)
            
        target_cmd = [sys.executable, os.path.realpath(sys.argv[0])] + escalated_args
        
        # 1. Non-Interactive Check: If the user recently used sudo, just reuse the token silently.
        has_silent_sudo = False
        if shutil.which("sudo"):
            try:
                if subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=2).returncode == 0:
                    has_silent_sudo = True
            except Exception: pass

        if has_silent_sudo:
            cmd = ["sudo", "env"] + env_args + target_cmd
            # os.execvp completely replaces the current process without waiting. TTY control is seamlessly transferred.
            os.execvp(cmd[0], cmd)
            
        # 2. Terminal Auth: Direct sudo prompt (no Polkit dependency)
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
    # --- 2. MULTI-ENGINE ARCHITECTURE & ROUTER BLOCK ---
    # =========================================================================
    engine_pool = {}

    def get_engine_instance(e_type: str, config_path: str):
        key = (e_type, config_path)
        if key in engine_pool: return engine_pool[key]
        
        if e_type == "lua":
            from python.engines.lua import HyprlandLuaEngine
            engine = HyprlandLuaEngine(config_path=config_path)
        elif e_type == "trackpad":
            from python.engines.trackpad import TrackpadLuaEngine
            engine = TrackpadLuaEngine(config_path=config_path)
        elif e_type == "monitor":
            from python.engines.monitor_engine import MonitorLuaEngine
            engine = MonitorLuaEngine(config_path=config_path)
        elif e_type == "ini":
            from python.engines.ini import IniConfigEngine
            engine = IniConfigEngine(config_path=config_path)
        elif e_type == "bridged_ini":
            from python.engines.bridged_ini import BridgedIniEngine
            engine = BridgedIniEngine(config_path=config_path)
        elif e_type == "systemd":
            from python.engines.systemd import SystemdEngine
            engine = SystemdEngine()
        elif e_type == "hyprlang":
            from python.engines.hyprlang import HyprlangEngine
            engine = HyprlangEngine(config_path=config_path)
        elif e_type == "cmdline":
            from python.engines.cmdline import CmdlineEngine
            engine = CmdlineEngine(config_path=config_path)
        elif e_type == "systemd_boot":
            from python.engines.systemd_boot import SystemdBootEngine
            engine = SystemdBootEngine(config_path=config_path)
        elif e_type == "flatdotconfig":
            from python.engines.flatdotconfig import FlatDotConfigEngine
            engine = FlatDotConfigEngine(config_path=config_path)
        elif e_type == "env":
            from python.engines.environment_variables import ShellEnvEngine
            engine = ShellEnvEngine(config_path=config_path)
        elif e_type == "shell_fallback":
            from python.engines.shell_fallback import ShellFallbackEngine
            engine = ShellFallbackEngine(config_path=config_path)
        elif e_type == "waybar":
            from python.engines.waybar_engine import WaybarEngine
            engine = WaybarEngine(config_path=config_path)
        elif e_type == "network":
            from python.engines.network_manager import NetworkManagerEngine
            engine = NetworkManagerEngine(config_path=config_path)
        elif e_type == "pkg_throttle":
            from python.engines.pkg_throttle import PkgThrottleEngine
            engine = PkgThrottleEngine(config_path=config_path)
        elif e_type == "cpu_core":
            from python.engines.cpu_core import CpuCoreEngine
            engine = CpuCoreEngine(config_path=config_path)
        elif e_type == "fstab":
            from python.engines.fstab import FstabEngine
            engine = FstabEngine(config_path=config_path)
        elif e_type == "json":
            from python.engines.json_engine import JsonEngine
            engine = JsonEngine(config_path=config_path)
        else:
            print(f"[-] Fatal: Unknown ENGINE_TYPE '{e_type}' specified in schema '{schema_path.name}'.")
            print("[i] Supported engines are: 'lua', 'ini', 'bridged_ini', 'systemd', 'hyprlang', 'trackpad', 'monitor', 'cmdline', 'systemd_boot', 'flatdotconfig', 'env', 'waybar', 'network', 'pkg_throttle', 'cpu_core', 'fstab', 'shell_fallback', 'json'")
            sys.exit(1)

        engine_pool[key] = engine
        return engine

    # Prime the primary engine target
    default_engine_key = (ENGINE_TYPE, str(TARGET_FILE))
    get_engine_instance(*default_engine_key)

    # Pre-instantiate overridden engines targeted across the schema
    for tab_idx, items in SCHEMA.items():
        for item in items:
            if getattr(item, "engine_type_override", None) or getattr(item, "target_file_override", None):
                override_etype = (item.engine_type_override or ENGINE_TYPE).lower()
                override_tfile = str(Path(item.target_file_override).expanduser().resolve()) if item.target_file_override else str(TARGET_FILE)
                get_engine_instance(override_etype, override_tfile)


    # --- 3. PRE-FLIGHT CHECKS (Backups / Restores) ---
    is_headless = any([args.default, args.reset_key, args.set, args.export_state, args.export_docs])

    # Extract ALL unique target files from the schema to ensure total backup coverage
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
        # Complete deferred schema loading before headless processing
        if DEFERRED_LOAD:
            DEFERRED_LOAD()

        # Pre-load state caches across all active backend targets
        for eng in engine_pool.values():
            eng.load_state()

        if args.export_state:
            merged_state = {}
            for ekey, eng in engine_pool.items():
                st = eng.cache if hasattr(eng, "cache") else eng.load_state()
                if ekey == default_engine_key:
                    merged_state.update(st)
                else:
                    file_path = Path(ekey[1])
                    path_hash = hashlib.md5(str(file_path.resolve()).encode()).hexdigest()[:4]
                    safe_namespace = f"{file_path.parent.name}_{file_path.name}_{path_hash}"
                    
                    for k, v in st.items():
                        merged_state[f"{safe_namespace}::{k}"] = v
                        
            print(json.dumps(merged_state, indent=2))
            sys.exit(0)

        if args.export_docs:
            print(f"# Configuration Reference: {APP_TITLE}\n")
            for tab_idx, items in SCHEMA.items():
                print(f"## {TABS[tab_idx]}")
                for item in items:
                    if item.type_ in ("action", "preset", "menu"): continue
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
            t_file = str(Path(item.target_file_override).expanduser().resolve()) if item.target_file_override else str(TARGET_FILE)
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
            t_file = str(Path(item.target_file_override).expanduser().resolve()) if item.target_file_override else str(TARGET_FILE)
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
                t_file = str(Path(item.target_file_override).expanduser().resolve()) if item.target_file_override else str(TARGET_FILE)
                ekey = (e_type, t_file)
                
                if ekey not in changes_by_engine: changes_by_engine[ekey] = []
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
                        if ok: success_count += 1
                        else: skip_count += 1
                    
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
        deferred_load=DEFERRED_LOAD
    )
    
    for engine in engine_pool.values():
        if hasattr(engine, "set_app"):
            engine.set_app(app)
            
    app.run()
