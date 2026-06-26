#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: WINDOW RULES CONFIGURATION SCHEMA
===============================================================================
"""

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "lua"
TARGET_FILE = "~/.config/hypr/edit_here/source/window_rules.lua"
APP_TITLE = "Window & Layout Rules"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "1. General & Display",
    "2. Window Focus Behavior",
    "3. Profiles & Advanced"
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: GENERAL & DISPLAY
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Magic Workspace Primary Color",
            key="primary_color",
            scope="DEFAULT",          # UID = "primary_color" (Root level local variable)
            type_="string",           # Using string to retain precise rgb() formatting
            default="rgb(E2971F)",
            group="Special Workspaces",
            extended_help="**Magic Workspace Color**\n\nDefines the fallback/primary border color applied specifically to the 'special:magic' workspace when windows are moved there."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: WINDOW FOCUS BEHAVIOR
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Focus Under Fullscreen",
            key="on_focus_under_fullscreen",
            scope="misc",             # UID = "misc.on_focus_under_fullscreen"
            type_="int",
            default=2,
            options=[0, 1, 2],
            group="Fullscreen Management",
            extended_help="**Fullscreen App Focus Rules**\n\nDetermines behavior when a background app requests focus while another app is fullscreen.\n\n- `0` = **Do Nothing**: Opens invisibly behind the fullscreen app.\n- `1` = **Overlay**: Opens on top, but the background app remains fullscreen.\n- `2` = **Unfullscreen**: Forces the fullscreen app to exit fullscreen and switches focus."
        ),
        ConfigItem(
            label="Initial Workspace Tracking",
            key="initial_workspace_tracking",
            scope="misc",             # UID = "misc.initial_workspace_tracking"
            type_="int",
            default=1,
            options=[0, 1, 2],
            group="Fullscreen Management",
            extended_help="**Workspace Tracking**\n\nRequired to force new windows to spawn on the *current* workspace. Without this set to `1`, background tasks might fail to trigger the unfullscreen event properly."
        ),
        ConfigItem(
            label="Focus on Activate",
            key="focus_on_activate",
            scope="misc",             # UID = "misc.focus_on_activate"
            type_="bool",
            default=True,
            group="Window Activation",
            extended_help="**Application Focus Requests**\n\nAllows applications (like game launchers or Steam) to forcefully request and steal focus. Useful for apps that spawn multiple chained windows."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: PROFILES & ADVANCED
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Strict Focus Profile",
            key="preset_strict_focus",
            scope="DEFAULT",          # UID = "preset_strict_focus"
            type_="preset",
            default=None,
            group="Configuration Profiles",
            preset_payload={
                "misc.on_focus_under_fullscreen": 2,
                "misc.initial_workspace_tracking": 1,
                "misc.focus_on_activate": True
            },
            extended_help="**Strict Focus**\n\nApplies the default recommended behavior where popups immediately drop fullscreen apps to reveal the newly focused window."
        ),
        ConfigItem(
            label="Immersive/Do Not Disturb Profile",
            key="preset_immersive_focus",
            scope="DEFAULT",          # UID = "preset_immersive_focus"
            type_="preset",
            default=None,
            group="Configuration Profiles",
            preset_payload={
                "misc.on_focus_under_fullscreen": 0,
                "misc.focus_on_activate": False
            },
            extended_help="**Immersive Profile**\n\nPrevents any background application from stealing focus or dropping your current fullscreen application. Popups will spawn silently behind your game or video."
        ),
        ConfigItem(
            label="Reload Window Rules",
            key="action_reload_hypr",
            scope="DEFAULT",          # UID = "action_reload_hypr"
            type_="action",
            default="hyprctl reload",
            group="System Actions",
            extended_help="**Reload Environment**\n\nForces Hyprland to re-read all window rules and configuration files without terminating the session."
        ),
        ConfigItem(
            label="Factory Reset Rules",
            key="preset_factory_reset",
            scope="DEFAULT",          # UID = "preset_factory_reset"
            type_="preset",
            default=None,
            group="System Actions",
            preset_payload={
                "__ALL_DEFAULTS__": True
            }
        ),
    ]
}
