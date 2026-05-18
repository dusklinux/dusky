#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: TRACKPAD GESTURES CONFIGURATION SCHEMA
===============================================================================
"""

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING (REQUIRED)
# =============================================================================
ENGINE_TYPE = "trackpad"
TARGET_FILE = "~/.config/hypr/edit_here/source/trackpad.lua"
APP_TITLE = "Trackpad Gestures"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Physics",
    "Navigation",
    "Actions",
    "Profiles"
]

# =============================================================================
# 4. SHARED GESTURE OPTIONS (Clean UI Rendering)
# =============================================================================
# The custom Trackpad engine handles translating these labels into Lua code.
# =============================================================================
GESTURE_OPTIONS = [
    "Native Workspace Swipe",
    "Toggle Dusky QuickPanel",
    "Toggle Waybar",
    "Toggle Blur & Opacity",
    "Media: Play / Pause",
    "Media: Volume Up (+10%)",
    "Media: Volume Down (-10%)",
    "Screen: Brightness Up (+10%)",
    "Screen: Brightness Down (-10%)",
    "Disabled / Unbound"
]

GESTURE_HINTS = [
    "Smooth 1:1 workspace switching",
    "Opens the custom quick panel",
    "Toggles the Waybar panel",
    "Toggles Hyprland visual effects",
    "Play/Pause current media",
    "Increases volume by 10%",
    "Decreases volume by 10%",
    "Increases brightness by 10%",
    "Decreases brightness by 10%",
    "Removes action mapping"
]

# =============================================================================
# 5. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: GESTURE PHYSICS
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Swipe Distance",
            key="workspace_swipe_distance",
            scope="gestures",
            type_="int",
            default=300,
            min_val=50,
            max_val=1500,
            step=50,
            group="Distance",
            extended_help="**Swipe Distance**\n\nMaximum swipe travel distance in pixels required to trigger a full workspace transition."
        ),
        ConfigItem(
            label="Commit Cancel Ratio",
            key="workspace_swipe_cancel_ratio",
            scope="gestures",
            type_="float",
            default=0.5,
            min_val=0.0,
            max_val=1.0,
            step=0.1,
            group="Distance",
            extended_help="**Cancel Ratio**\n\nThe fraction of the total swipe distance needed to commit to a workspace switch (0.0 to 1.0). If you lift your fingers before this threshold, the workspace snaps back."
        ),
        ConfigItem(
            label="Min Speed to Force Switch",
            key="workspace_swipe_min_speed_to_force",
            scope="gestures",
            type_="int",
            default=30,
            min_val=0,
            max_val=200,
            step=5,
            group="Distance",
            extended_help="**Force Speed**\n\nMinimum speed (in pixels per timepoint) required to force a workspace change regardless of the cancel ratio. Set to 0 to disable."
        ),
        ConfigItem(
            label="1:1 Gesture Close Timeout",
            key="close_max_timeout",
            scope="gestures",
            type_="int",
            default=1000,
            min_val=0,
            max_val=5000,
            step=100,
            group="Timing",
            extended_help="**Close Max Timeout**\n\nMaximum time in milliseconds a 1:1 gesture window has to close."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: ADVANCED NAVIGATION
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Invert Swipe Direction",
            key="workspace_swipe_invert",
            scope="gestures",
            type_="bool",
            default=True,
            group="Behavior",
            extended_help="**Invert Direction**\n\nInverts the swipe direction. When enabled, this mimics 'natural scrolling' on macOS and modern trackpad drivers."
        ),
        ConfigItem(
            label="Create New Workspace on Swipe",
            key="workspace_swipe_create_new",
            scope="gestures",
            type_="bool",
            default=True,
            group="Behavior",
            extended_help="**Create New Workspace**\n\nAutomatically creates a new, empty workspace when swiping past the last active workspace."
        ),
        ConfigItem(
            label="Allow Swiping Forever",
            key="workspace_swipe_forever",
            scope="gestures",
            type_="bool",
            default=False,
            group="Behavior",
            extended_help="**Swipe Forever**\n\nAllows you to continuously swipe past neighbouring workspaces without stopping at the edge."
        ),
        ConfigItem(
            label="Use Relative Workspaces ('r' prefix)",
            key="workspace_swipe_use_r",
            scope="gestures",
            type_="bool",
            default=False,
            group="Behavior",
            extended_help="**Relative Workspaces**\n\nUses the 'r' prefix (relative) instead of the 'm' prefix when switching workspaces. Useful for specific multi-monitor behaviors."
        ),
        ConfigItem(
            label="Enable Touchscreen Swiping",
            key="workspace_swipe_touch",
            scope="gestures",
            type_="bool",
            default=False,
            group="Touchscreen",
            extended_help="**Touchscreen Swiping**\n\nEnables workspace swiping from the edge of a physical touchscreen display."
        ),
        ConfigItem(
            label="Invert Touchscreen Direction",
            key="workspace_swipe_touch_invert",
            scope="gestures",
            type_="bool",
            default=False,
            group="Touchscreen",
            extended_help="**Invert Touchscreen Direction**\n\nInverts the direction of workspace swipes performed directly on a touchscreen."
        ),
        
        # --- HYBRID LOCK MENU ---
        ConfigItem(
            label="Lock Swipe Direction",
            key="workspace_swipe_direction_lock",
            scope="gestures",
            type_="bool",
            default=True,
            is_parent=True,
            expanded=False,
            group="Locking",
            extended_help="**Direction Lock**\n\nLocks the swipe axis (horizontal or vertical) once the initial direction is established, preventing diagonal drifting."
        ),
        ConfigItem(
            label="Direction Lock Threshold",
            key="workspace_swipe_direction_lock_threshold",
            scope="gestures",
            type_="int",
            default=10,
            min_val=0,
            max_val=100,
            step=2,
            parent_ref="gestures.workspace_swipe_direction_lock",
            extended_help="**Lock Threshold**\n\nDistance in pixels the swipe must travel before the direction lock fully engages."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: ACTIONS (Custom Gesture Bindings)
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="3-Finger Swipe (Horizontal 1:1)",
            key="action",
            scope="gesture/3/horizontal",
            type_="picker",
            default="Native Workspace Swipe",
            options=GESTURE_OPTIONS,
            hints=GESTURE_HINTS,
            group="Trio",
            extended_help="**3-Finger Horizontal Swipe (Continuous)**\n\nAssigns an action to continuous horizontal swiping. Native workspace swiping is heavily recommended here to preserve 1:1 tracking.\n\n*Note: To use discrete Left/Right bash scripts instead, set this to 'Disabled / Unbound' first to prevent conflicts.*"
        ),
        ConfigItem(
            label="3-Finger Swipe Left (Discrete)",
            key="action",
            scope="gesture/3/left",
            type_="picker",
            default="Disabled / Unbound",
            options=GESTURE_OPTIONS,
            hints=GESTURE_HINTS,
            group="Trio",
            extended_help="**3-Finger Swipe Left (Discrete)**\n\nAssigns an action specifically to a discrete leftward swipe. \n\n*Requires 'Horizontal 1:1' to be Unbound to prevent tracking conflicts.*"
        ),
        ConfigItem(
            label="3-Finger Swipe Right (Discrete)",
            key="action",
            scope="gesture/3/right",
            type_="picker",
            default="Disabled / Unbound",
            options=GESTURE_OPTIONS,
            hints=GESTURE_HINTS,
            group="Trio",
            extended_help="**3-Finger Swipe Right (Discrete)**\n\nAssigns an action specifically to a discrete rightward swipe. \n\n*Requires 'Horizontal 1:1' to be Unbound to prevent tracking conflicts.*"
        ),
        ConfigItem(
            label="3-Finger Swipe Up",
            key="action",
            scope="gesture/3/up",
            type_="picker",
            default="Toggle Dusky QuickPanel",
            options=GESTURE_OPTIONS,
            hints=GESTURE_HINTS,
            group="Trio",
            extended_help="**3-Finger Swipe Up**\n\nAssigns an executable script or action to the upward swipe gesture using three fingers."
        ),
        ConfigItem(
            label="3-Finger Swipe Down",
            key="action",
            scope="gesture/3/down",
            type_="picker",
            default="Media: Play / Pause",
            options=GESTURE_OPTIONS,
            hints=GESTURE_HINTS,
            group="Trio",
            extended_help="**3-Finger Swipe Down**\n\nAssigns an executable script or action to the downward swipe gesture using three fingers."
        ),
        ConfigItem(
            label="4-Finger Swipe Left",
            key="action",
            scope="gesture/4/left",
            type_="picker",
            default="Media: Volume Down (-10%)",
            options=GESTURE_OPTIONS,
            hints=GESTURE_HINTS,
            group="Quad",
            extended_help="**4-Finger Swipe Left**\n\nAssigns an executable script or action to the left swipe gesture using four fingers."
        ),
        ConfigItem(
            label="4-Finger Swipe Right",
            key="action",
            scope="gesture/4/right",
            type_="picker",
            default="Media: Volume Up (+10%)",
            options=GESTURE_OPTIONS,
            hints=GESTURE_HINTS,
            group="Quad",
            extended_help="**4-Finger Swipe Right**\n\nAssigns an executable script or action to the right swipe gesture using four fingers."
        ),
        ConfigItem(
            label="4-Finger Swipe Up",
            key="action",
            scope="gesture/4/up",
            type_="picker",
            default="Screen: Brightness Up (+10%)",
            options=GESTURE_OPTIONS,
            hints=GESTURE_HINTS,
            group="Quad",
            extended_help="**4-Finger Swipe Up**\n\nAssigns an executable script or action to the upward swipe gesture using four fingers."
        ),
        ConfigItem(
            label="4-Finger Swipe Down",
            key="action",
            scope="gesture/4/down",
            type_="picker",
            default="Screen: Brightness Down (-10%)",
            options=GESTURE_OPTIONS,
            hints=GESTURE_HINTS,
            group="Quad",
            extended_help="**4-Finger Swipe Down**\n\nAssigns an executable script or action to the downward swipe gesture using four fingers."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: PROFILES & RESET
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Apply 'Fast & Fluid' Profile",
            key="preset_fluid",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Profiles",
            preset_payload={
                "gestures.workspace_swipe_distance": 200,
                "gestures.workspace_swipe_cancel_ratio": 0.3,
                "gestures.workspace_swipe_min_speed_to_force": 20,
                "gestures.workspace_swipe_direction_lock": False
            },
            extended_help="**Fast & Fluid**\n\nRequires very little finger travel to switch workspaces. Disables axis locking for a looser, more sensitive feel."
        ),
        ConfigItem(
            label="Apply 'Firm & Intentional' Profile",
            key="preset_firm",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Profiles",
            preset_payload={
                "gestures.workspace_swipe_distance": 500,
                "gestures.workspace_swipe_cancel_ratio": 0.7,
                "gestures.workspace_swipe_min_speed_to_force": 60,
                "gestures.workspace_swipe_direction_lock": True,
                "gestures.workspace_swipe_direction_lock_threshold": 25
            },
            extended_help="**Firm & Intentional**\n\nRequires long, deliberate swipes to trigger a workspace change. Highly resistant to accidental triggers."
        ),
        ConfigItem(
            label="Factory Reset Everything",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Reset",
            preset_payload={
                "__ALL_DEFAULTS__": True
            },
            extended_help="**Factory Reset**\n\nReverts all settings across all tabs back to their default values."
        ),
    ]
}
