#!/usr/bin/env python3
import sys
import os
from pathlib import Path

# =============================================================================
# CACHE CONFIGURATION
# Redirect __pycache__ creation to a centralized XDG cache directory.
# MUST be done before importing custom modules.
# =============================================================================
def _setup_cache() -> None:
    try:
        xdg_cache_env = os.environ.get("XDG_CACHE_HOME", "").strip()
        xdg_cache = Path(xdg_cache_env) if xdg_cache_env else Path.home() / ".cache"
        cache_dir = xdg_cache / "dusky_tui"
        cache_dir.mkdir(parents=True, exist_ok=True)
        sys.pycache_prefix = str(cache_dir)
    except OSError:
        pass

_setup_cache()

# =============================================================================
# 1. Path Injection (IoC Setup)
# Ensures the runner can find the ecosystem without hardcoded system installs.
# =============================================================================
TEMPLATE_DIR = Path("~/user_scripts/dusky_tui").expanduser().resolve()
if str(TEMPLATE_DIR) not in sys.path:
    sys.path.insert(0, str(TEMPLATE_DIR))

# =============================================================================
# 2. Lazy Import Architectural Components
# =============================================================================
from python.frontend.core_types import ConfigItem
from python.engines.lua import HyprlandLuaEngine
from python.frontend.ui import DuskyTUI

# =============================================================================
# 3. Dynamic Schema Construction
# Defines the exhaustive parameters for the entire input.lua domain + Stress tests.
# Notice: Type-purity restored. Integers are integers, booleans are booleans.
# =============================================================================
TABS = [
    "Keyboard", 
    "Mouse & Pointer", 
    "Touchpad & Scrolling", 
    "Cursor Behavior", 
    "Gestures", 
    "Stress Test (Ghost Data)"
]

SCHEMA = {
    0: [
        ConfigItem(label="Keyboard Layout", key="kb_layout", scope="input", type_="string", default="us"),
        ConfigItem(label="Keyboard Options", key="kb_options", scope="input", type_="string", default=""),
        ConfigItem(label="Resolve by Sym", key="resolve_binds_by_sym", scope="input", type_="bool", default=False),
        ConfigItem(label="Numlock by Default", key="numlock_by_default", scope="input", type_="bool", default=True),
        ConfigItem(label="Repeat Rate", key="repeat_rate", scope="input", type_="int", default=35, min_val=10, max_val=100, step=5),
        ConfigItem(label="Repeat Delay", key="repeat_delay", scope="input", type_="int", default=250, min_val=100, max_val=1000, step=50),
    ],
    1: [
        ConfigItem(label="Follow Mouse", key="follow_mouse", scope="input", type_="cycle", default=1, options=[0, 1, 2, 3]),
        ConfigItem(label="Mouse Sensitivity", key="sensitivity", scope="input", type_="float", default=0.0, min_val=-1.0, max_val=1.0, step=0.1),
        ConfigItem(label="Accel Profile", key="accel_profile", scope="input", type_="cycle", default="adaptive", options=["adaptive", "flat", "custom"]),
        ConfigItem(label="Force No Accel", key="force_no_accel", scope="input", type_="bool", default=False),
        ConfigItem(label="Left Handed Mode", key="left_handed", scope="input", type_="bool", default=False),
        ConfigItem(label="Mouse Refocus", key="mouse_refocus", scope="input", type_="bool", default=True),
    ],
    2: [
        ConfigItem(label="Global Natural Scroll", key="natural_scroll", scope="input", type_="bool", default=False),
        ConfigItem(label="Scroll Method", key="scroll_method", scope="input", type_="cycle", default="2fg", options=["2fg", "edge", "on_button_down", "none"]),
        ConfigItem(label="Scroll Button", key="scroll_button", scope="input", type_="int", default=0, min_val=0, max_val=255, step=1),
        ConfigItem(label="Scroll Button Lock", key="scroll_button_lock", scope="input", type_="bool", default=False),
        
        # Nested Touchpad Scope mapped seamlessly to input.lua
        ConfigItem(label="TP Natural Scroll", key="natural_scroll", scope="input/touchpad", type_="bool", default=True),
        ConfigItem(label="Disable While Typing", key="disable_while_typing", scope="input/touchpad", type_="bool", default=True),
        ConfigItem(label="Tap to Click", key="tap_to_click", scope="input/touchpad", type_="bool", default=True),
        ConfigItem(label="Clickfinger Behavior", key="clickfinger_behavior", scope="input/touchpad", type_="bool", default=False),
        ConfigItem(label="Drag Lock Timeout", key="drag_lock", scope="input/touchpad", type_="cycle", default=0, options=[0, 1, 2]),
    ],
    3: [
        ConfigItem(label="Sync GSettings Theme", key="sync_gsettings_theme", scope="cursor", type_="bool", default=True),
        ConfigItem(label="No Hardware Cursors", key="no_hardware_cursors", scope="cursor", type_="cycle", default=2, options=[0, 1, 2]),
        ConfigItem(label="Use CPU Buffer", key="use_cpu_buffer", scope="cursor", type_="cycle", default=2, options=[0, 1, 2]),
        ConfigItem(label="Hide on Key Press", key="hide_on_key_press", scope="cursor", type_="bool", default=False),
        ConfigItem(label="Inactive Timeout", key="inactive_timeout", scope="cursor", type_="int", default=0, min_val=0, max_val=60, step=1),
        ConfigItem(label="Warp on Workspace", key="warp_on_change_workspace", scope="cursor", type_="cycle", default=0, options=[0, 1, 2]),
        ConfigItem(label="No Break FS VRR", key="no_break_fs_vrr", scope="cursor", type_="cycle", default=2, options=[0, 1, 2]),
        ConfigItem(label="Zoom Factor", key="zoom_factor", scope="cursor", type_="float", default=1.0, min_val=0.1, max_val=5.0, step=0.1),
    ],
    4: [
        ConfigItem(label="Swipe Distance", key="workspace_swipe_distance", scope="gestures", type_="int", default=300, min_val=100, max_val=1000, step=50),
        ConfigItem(label="Swipe Cancel Ratio", key="workspace_swipe_cancel_ratio", scope="gestures", type_="float", default=0.5, min_val=0.1, max_val=1.0, step=0.1),
        ConfigItem(label="Swipe Invert", key="workspace_swipe_invert", scope="gestures", type_="bool", default=True),
        ConfigItem(label="Swipe Create New", key="workspace_swipe_create_new", scope="gestures", type_="bool", default=True),
        ConfigItem(label="Swipe Forever", key="workspace_swipe_forever", scope="gestures", type_="bool", default=False),
    ],
    5: [
        # Stress Test Elements (Non-existent keys that the UI will fallback to default values for)
        ConfigItem(label="Dummy String", key="dummy_str", scope="stress", type_="string", default="Hello Arch"),
        ConfigItem(label="Dummy Float", key="dummy_float", scope="stress", type_="float", default=42.0, min_val=0.0, max_val=100.0, step=1.0),
        ConfigItem(label="Dummy Integer", key="dummy_int", scope="stress", type_="int", default=9000, min_val=0, max_val=10000, step=10),
        ConfigItem(label="Dummy Boolean", key="dummy_bool", scope="stress", type_="bool", default=False),
        ConfigItem(label="Dummy Cycle", key="dummy_cycle", scope="stress", type_="cycle", default="Omega", options=["Alpha", "Beta", "Omega", "Zeta"]),
    ]
}

# =============================================================================
# 4. Bind & Execute
# =============================================================================
if __name__ == "__main__":
    # Link the native backend Engine to the specific target Lua file
    target_file = "~/.config/hypr/source/input.lua"
    engine = HyprlandLuaEngine(config_path=target_file)
    
    # Define the Matugen generated JSON path for hot-reloading native TUI colors
    theme_file = "~/.config/matugen/generated/dusky_tui.json"
    
    # Inject Engine, Schema, and Theme path into the decoupled TUI instance
    app = DuskyTUI(
        engine=engine, 
        schema=SCHEMA, 
        tabs=TABS, 
        title="Hyprland Input Configurator",
        theme_path=theme_file
    )
    
    # Launch application
    app.run()
