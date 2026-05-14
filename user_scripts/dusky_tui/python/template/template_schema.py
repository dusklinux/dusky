#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: MASTER CONFIGURATION SCHEMA
===============================================================================

TARGET MAPPING VISUALIZATION:
How `scope` and `key` tell the engine exactly what to edit in the target file:
    [theme.colors]               <-- scope="theme.colors" (Deep nesting supported)
    active_border = #ff89b4fa    <-- key="active_border"

READ BEFORE EDITING:
1. UID (Unique Identifier) Rule:
   - If a variable sits at the root of the file (no section), set scope="DEFAULT".
   - If scope is defined, the UID is `scope.key` (e.g., "theme.border_active").
   - You MUST use the exact UID when using `parent_ref` or `preset_payload`.

2. Grouping Rule:
   - Items with the same `group` string MUST be placed next to each other in 
     the list. The UI draws headers sequentially.

3. Available Types (`type_`):
   - "bool"   : Toggles instantly (True/False)
   - "int"    : Numeric integer (supports min_val, max_val, step)
   - "float"  : Numeric decimal (supports min_val, max_val, step)
   - "string" : Text input (opens a text overlay)
   - "cycle"  : Instant left/right cycling through an `options` list
   - "picker" : Opens a searchable fullscreen modal from an `options` list
   - "color"  : Hex, RGB, HSL, or Matugen theme variables
   - "menu"   : A visual folder to group child items (requires `is_parent=True`)
   - "action" : Triggers a shell command (put the command string in `default=`)
   - "preset" : Applies multiple values at once (requires `preset_payload`)

TESTING THIS SCHEMA:
  python main.py path/to/this_schema.py
===============================================================================
"""

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING (REQUIRED)
# =============================================================================
ENGINE_TYPE = "ini"                        # Supported: "ini", "lua"
TARGET_FILE = "~/.config/app/config.ini"   # Where the engine writes the data
APP_TITLE = "Master Configurator"          # Displayed in the TUI border

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR (OPTIONAL)
# =============================================================================
DEFAULT_MODE = "auto"                      # "auto" (instant save) | "batch" (Ctrl+S required)
THEME_FILE = "~/.config/matugen/dusky.json" # Matugen color map (Optional)

# =============================================================================
# 3. TABS DEFINITION
# Arrays in SCHEMA map directly to the index of these tabs.
# =============================================================================
TABS = [
    "1. General Settings", 
    "2. Appearance & Menus", 
    "3. Profiles & Actions"
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: STANDARD DATA TYPES
    # -------------------------------------------------------------------------
    0: [
        # Example of a root-level variable (Not inside any [section])
        ConfigItem(
            label="Enable Global Logging",
            key="logging",
            scope="DEFAULT",       # UID = "logging"
            type_="bool",
            default=False,
            group="System Variables", 
        ),
        ConfigItem(
            label="Enable Animations",
            key="animations",
            scope="core",          # UID = "core.animations"
            type_="bool",
            default=True,
            group="System Variables", 
            extended_help="**Animations**\n\nToggles UI animations globally."
        ),
        ConfigItem(
            label="Window Gaps",
            key="gaps_in",
            scope="layout",        # UID = "layout.gaps_in"
            type_="int",
            default=5,
            min_val=0,
            max_val=50,
            step=1,
            group="System Variables" # Must stay adjacent to share the same header
        ),
        # You can pass options=[] to an int/float to lock the arrow keys to specific numbers!
        ConfigItem(
            label="Locked Border Size",
            key="locked_border",
            scope="layout",        # UID = "layout.locked_border"
            type_="int",
            default=2,
            options=[0, 2, 5, 8, 15], # Arrow keys snap exactly to these values
            group="System Variables"
        ),
        ConfigItem(
            label="User Greeting",
            key="greeting",
            scope="core",          # UID = "core.greeting"
            type_="string",
            default="Welcome back!",
            group="Text Overrides"
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: UI COMPONENTS & NESTED FOLDERS
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Active Border Color",
            key="border_active",
            scope="theme",         # UID = "theme.border_active"
            type_="color",
            default="#a8c8ff",
            group="Theming"
        ),
        # Using options=[] on a color type constrains the user to theme aliases!
        ConfigItem(
            label="Inactive Border Color",
            key="border_inactive",
            scope="theme",         # UID = "theme.border_inactive"
            type_="color",
            default="#414453",
            options=["background", "surface", "primary", "error"], 
            group="Theming"
        ),
        ConfigItem(
            label="Border Style",
            key="border_style",
            scope="theme",         # UID = "theme.border_style"
            type_="cycle",         
            default="solid",
            options=["solid", "dashed", "dotted", "hidden"],
            group="Theming"
        ),
        
        # --- HIERARCHY / NESTED MENU IMPLEMENTATION ---
        # 1. The Parent Folder (Does not write to backend)
        ConfigItem(
            label="Typography Settings",
            key="typography_menu",
            scope="DEFAULT",       # UID = "typography_menu"
            type_="menu",          
            default=None,
            is_parent=True,        # CRITICAL: Flags this item as an expandable folder
            expanded=False,        # Starts collapsed
            group="Fonts"
        ),
        # 2. Child Item A
        ConfigItem(
            label="System Font Family",
            key="font_family",
            scope="fonts",         # UID = "fonts.font_family"
            type_="picker",        
            default="JetBrains Mono",
            options=["JetBrains Mono", "Fira Code", "Roboto"],
            hints=["Monospace", "Ligatures", "Sans-Serif"], # Hints map 1:1 with options
            parent_ref="typography_menu"  # CRITICAL: Links directly to parent's UID
        ),
        # 3. Child Item B
        ConfigItem(
            label="Font Size",
            key="font_size",
            scope="fonts",         # UID = "fonts.font_size"
            type_="float",        
            default=11.0,
            min_val=8.0,
            max_val=24.0,
            step=0.5,
            parent_ref="typography_menu"  # Continues the visual tree line (├─ / └─)
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: ADVANCED CONTROLS (Presets & Actions)
    # -------------------------------------------------------------------------
    2: [
        # ACTION: Does not save to config. The `default` string is the shell command.
        ConfigItem(
            label="Clear System Cache",
            key="clear_cache",
            scope="system",        # UID = "system.clear_cache"
            type_="action",
            default="rm -rf ~/.cache/app_name/* && echo 'Cache Cleared'",
            group="Maintenance",
        ),
        
        # PRESET: Injects multiple specific values across different tabs/scopes.
        ConfigItem(
            label="Apply 'Performance' Profile",
            key="preset_performance",
            scope="DEFAULT",       # UID = "preset_performance"
            type_="preset",
            default=None,
            group="Profiles",
            preset_payload={
                # MUST use exact UIDs from the schema above
                "core.animations": False,      
                "layout.gaps_in": 0,           
                "theme.border_style": "solid"  
            }
        ),
        
        # FACTORY RESET PRESET: Magic payload to revert all items to their `default`
        ConfigItem(
            label="Factory Reset Everything",
            key="preset_factory_reset",
            scope="DEFAULT",       # UID = "preset_factory_reset"
            type_="preset",
            default=None,
            group="Profiles",
            preset_payload={
                "__ALL_DEFAULTS__": True
            }
        ),
    ]
}

# =============================================================================
# QUICK-REFERENCE CHEAT SHEET
# =============================================================================
# Copy/Paste this block when building new items to ensure correct kwargs.
#
# ConfigItem(
#     label          = "Display Name",
#     key            = "backend_key",
#     scope          = "DEFAULT",          # "backend_section" or "DEFAULT" for root
#     type_          = "bool",             # bool | int | float | string | cycle |
#                                          # color | picker | action | preset | menu
#     default        = None,               # Fallback/Reset value or shell command for 'action'
#     options        = [],                 # Required for cycle/picker. Locks arrow keys for int/color.
#     hints          = [],                 # Subtitles for picker modals (must match len(options))
#     preset_payload = {},                 # Dict of {"scope.key": value} for 'preset' type
#     min_val        = None,               # Numeric bounds
#     max_val        = None,               # Numeric bounds
#     step           = None,               # Numeric adjust step
#     group          = None,               # Section header string in UI
#     extended_help  = None,               # Markdown string for the help panel
#     is_parent      = False,              # Set True if type_="menu"
#     parent_ref     = None,               # Set to parent's UID to nest this item
#     expanded       = False,              # Default state for parent menus
# )
