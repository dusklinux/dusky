#!/usr/bin/env python3
import sys
import os
import argparse
import importlib.util
import json
import shutil
import logging
from datetime import datetime
from pathlib import Path

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
    """Handles creating and restoring backups without hard exiting, allowing CLI composition."""
    backup_dir = Path("~/Documents/dusky_backups/tui_reset/").expanduser()
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{target_file.name}.{timestamp}.bak"
    latest_link = backup_dir / f"{target_file.name}.latest.bak"

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
            print("[-] No backup found to restore.")
            return False
        
        shutil.copy2(latest_link, target_file)
        logger.info(f"Restored from backup: {latest_link}")
        print(f"[+] Successfully restored configuration from backup.")
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
        
        # STRICT REQUIREMENT: The schema MUST explicitly define ENGINE_TYPE.
        # We access it directly so it throws an AttributeError if it's missing.
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

    # --- 2. PRE-FLIGHT CHECKS (Backups / Restores) ---
    is_headless = any([args.default, args.reset_key, args.set, args.export_state, args.export_docs])

    if args.restore:
        if not manage_backup(TARGET_FILE, "restore", logger):
            sys.exit(1)
        if not is_headless and not args.backup:
            sys.exit(0)
            
    if args.backup:
        manage_backup(TARGET_FILE, "create", logger)
        if not is_headless:
            sys.exit(0)

    # =========================================================================
    # --- 3. INSTANTIATE ENGINE (ROUTER BLOCK) ---
    # =========================================================================
    # ADD NEW ENGINES HERE
    # To add a new engine in the future:
    # 1. Create your engine file in python/engines/ (e.g., yaml.py)
    # 2. Add an `elif ENGINE_TYPE == "yaml":` block below.
    # 3. Import your engine class locally inside that block.
    # 4. Instantiate it: engine = MyNewEngine(config_path=str(TARGET_FILE))
    # =========================================================================
    if ENGINE_TYPE == "lua":
        from python.engines.lua import HyprlandLuaEngine
        engine = HyprlandLuaEngine(config_path=str(TARGET_FILE))

    elif ENGINE_TYPE == "trackpad":
        from python.engines.trackpad import TrackpadLuaEngine
        engine = TrackpadLuaEngine(config_path=str(TARGET_FILE))

    elif ENGINE_TYPE == "monitor":
        from python.engines.monitor_engine import MonitorLuaEngine
        engine = MonitorLuaEngine(config_path=str(TARGET_FILE))

    elif ENGINE_TYPE == "ini":
        from python.engines.ini import IniConfigEngine
        engine = IniConfigEngine(config_path=str(TARGET_FILE))

    elif ENGINE_TYPE == "systemd":
        from python.engines.systemd import SystemdEngine
        engine = SystemdEngine()

    elif ENGINE_TYPE == "hyprlang":
        from python.engines.hyprlang import HyprlangEngine
        engine = HyprlangEngine(config_path=str(TARGET_FILE))

    else:
        print(f"[-] Fatal: Unknown ENGINE_TYPE '{ENGINE_TYPE}' specified in schema '{schema_path.name}'.")
        print("[i] Supported engines are: 'lua', 'ini'")
        sys.exit(1)

    # --- 4. HEADLESS OPERATIONS ---
    if is_headless:
        engine.load_state()

        if args.export_state:
            print(json.dumps(engine.cache, indent=2))
            sys.exit(0)

        if args.export_docs:
            print(f"# Configuration Reference: {APP_TITLE}\n")
            for tab_idx, items in SCHEMA.items():
                print(f"## {TABS[tab_idx]}")
                for item in items:
                    # STRICT UI TYPE EXCLUSION
                    if item.type_ in ("action", "preset", "menu"): continue
                    print(f"### `{item.key}`")
                    print(f"- **Type:** `{item.type_}`")
                    print(f"- **Default:** `{item.default}`")
                    if item.extended_help:
                        print(f"\n> {item.extended_help.replace('**', '')}\n")
            sys.exit(0)

        # Map both direct & compound keys safely
        flat_schema = {}
        for items in SCHEMA.values():
            for item in items:
                # STRICT UI TYPE EXCLUSION:
                # Prevent pure-UI structural macros from polluting backend state checks 
                # or getting written during a --default wipe.
                if item.type_ in ("action", "preset", "menu"):
                    continue
                
                scoped_key = f"{item.scope}.{item.key}"
                flat_schema[scoped_key] = item
                
                # Flag collision if multiple scopes share the same key
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

            # NATIVE SERIALIZATION (Handles __VAR__ wrappers and type coercion natively)
            val_str = item.serialize(val_str)

            logger.info(f"Headless Injection: {target_key} -> {val_str}")
            success, msg, _ = engine.write_value(item.key, item.scope, val_str, item_type=item.type_)
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

            # NATIVE SERIALIZATION 
            val = item.serialize(item.default)

            logger.info(f"Headless Reset Key: {args.reset_key} -> {val}")
            success, msg, _ = engine.write_value(item.key, item.scope, val, item_type=item.type_)
            print(f"[{'OK' if success else 'FAIL'}] {msg}")
            sys.exit(0 if success else 1)

        if args.default:
            logger.info("Initiating Full Headless Default Restoration")
            
            # Use id() mapping to ensure we don't duplicate executions for keys vs scoped_keys
            unique_items = {id(item): item for item in flat_schema.values() if item is not None}.values()
            
            changes_to_write = []
            
            for item in unique_items:
                # NATIVE SERIALIZATION 
                val = item.serialize(item.default)
                changes_to_write.append((item.key, item.scope, val, item.type_))
            
            # FULLY DEPLOY NATIVE BATCH ARCHITECTURE IN HEADLESS CLI
            success, msg, _ = engine.write_batch(changes_to_write)
            
            if success:
                print(f"[*] Restoration Complete. Reset {len(changes_to_write)} items successfully.")
                sys.exit(0)
            else:
                # Graceful fallback logic
                success_count, skip_count = 0, 0
                for key, scope, val, itype in changes_to_write:
                    ok, _, _ = engine.write_value(key, scope, val, item_type=itype)
                    if ok: success_count += 1
                    else: skip_count += 1
                
                print(f"[*] Partial Restoration Complete. Reset: {success_count} | Skipped: {skip_count}")
                sys.exit(0 if success_count > 0 else 1)


    # --- 5. INTERACTIVE TUI EXECUTION ---
    logger.info("Launching TUI")
    
    # DEFERRED IMPORT: Prevents UI dependencies from crashing the headless CLI 
    from python.frontend.ui import DuskyTUI
    
    app = DuskyTUI(
        engine=engine, 
        schema=SCHEMA, 
        tabs=TABS, 
        title=APP_TITLE,
        theme_path=THEME_FILE,
        default_mode=DEFAULT_MODE,
        schema_name=module_name,
        enable_user_presets=ENABLE_USER_PRESETS,
        user_presets_tab=USER_PRESETS_TAB
    )
    
    app.run()
