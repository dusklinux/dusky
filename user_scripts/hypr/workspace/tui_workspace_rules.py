#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: MASTER CONFIGURATION SCHEMA (WORKSPACE RULES)
===============================================================================
"""

import sys
from pathlib import Path

_dusky_root = Path.home() / "user_scripts" / "dusky_tui"
if str(_dusky_root) not in sys.path:
    sys.path.insert(0, str(_dusky_root))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "lua"
TARGET_FILE = "~/.config/hypr/edit_here/source/workspace_rules.lua"
APP_TITLE = "Hyprland Workspace Rules"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "General",
    "Workspaces",
    "Scrolling",
    "Dwindle",
    "Master",
    "Presets"
]

# =============================================================================
# 4. DYNAMIC WORKSPACE GENERATOR (TAB 1)
# =============================================================================
# Dynamically build the workspace 1-10 settings to give absolute per-workspace
# granularity over Section 1 in the target workspace_rules.lua.
WORKSPACE_ITEMS = []
for i in range(1, 11):
    WORKSPACE_ITEMS.extend([
        ConfigItem(
            label=f"Workspace {i} Settings",
            key=f"ws_{i}_menu",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=False,
            group="Workspaces"
        ),
        ConfigItem(
            label="Layout",
            key="layout",
            scope=f"workspace_rule/{i}",
            type_="cycle",
            default="scrolling" if i == 10 else "nil",
            options=["nil", "dwindle", "master", "scrolling", "monocle"],
            parent_ref=f"ws_{i}_menu",
            extended_help=f"**Workspace {i} Layout**\n\nForces a specific layout for workspace {i}. Set to 'scrolling' for paper tape scrolling layout, 'monocle' to fill the monitor, or 'nil' to inherit the Global Default Layout."
        ),
        ConfigItem(
            label="Persistent",
            key="persistent",
            scope=f"workspace_rule/{i}",
            type_="bool",
            default=(i == 10),
            parent_ref=f"ws_{i}_menu",
            extended_help=f"**Workspace {i} Persistence**\n\nKeeps workspace {i} alive in bars and pagers even when all windows inside it are closed."
        ),
    ])
    WORKSPACE_ITEMS.extend([
        ConfigItem(
            label='Monitor Binding',
            key='monitor',
            scope=f"workspace_rule/{i}",
            type_='string',
            default='',
            parent_ref=f"ws_{i}_menu",
            extended_help='Output name or desc:description; empty leaves the workspace unbound.'
        ),
        ConfigItem(
            label='Default on Monitor',
            key='default',
            scope=f"workspace_rule/{i}",
            type_='bool',
            default=False,
            parent_ref=f"ws_{i}_menu",
            extended_help='Make this the default workspace on its bound monitor.'
        ),
        ConfigItem(
            label='Workspace Name',
            key='default_name',
            scope=f"workspace_rule/{i}",
            type_='string',
            default='',
            parent_ref=f"ws_{i}_menu",
            extended_help='Default display name; empty keeps the numbered name.'
        ),
        ConfigItem(
            label='Launch on Empty Creation',
            key='on_created_empty',
            scope=f"workspace_rule/{i}",
            type_='string',
            default='',
            parent_ref=f"ws_{i}_menu",
            extended_help='Command to run when this workspace is created empty; empty launches nothing.'
        ),
    ])


# =============================================================================
# 5. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: GENERAL & BEHAVIOR
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Global Default Layout",
            key="layout",
            scope="general",
            type_="cycle",
            default="dwindle",
            options=["dwindle", "master", "scrolling", "monocle"],
            group="General Layout",
            extended_help="**Layout Override**\n\nSets the global default tiling layout for workspaces without specific overrides."
        ),
        ConfigItem(
            label="Focus & Miscellaneous",
            key="misc_menu_id",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=True,
            group="System Behavior"
        ),
        ConfigItem(
            label="Close Empty Special Workspaces",
            key="close_special_on_empty",
            scope="misc",
            type_="bool",
            default=True,
            parent_ref="misc_menu_id",
            extended_help="**Close Special on Empty**\n\nAuto-close a special workspace (scratchpad) when the last window in it is closed."
        ),
        ConfigItem(
            label="Focus on Activate Request",
            key="focus_on_activate",
            scope="misc",
            type_="bool",
            default=True,
            parent_ref="misc_menu_id",
            extended_help="**Focus on Activate**\n\nAutomatically focus a window that requests activation (e.g., urgency hint or xdg_activation)."
        ),
        ConfigItem(
            label="Focus Under Fullscreen",
            key="on_focus_under_fullscreen",
            scope="misc",
            type_="int",
            default=2,
            options=[0, 1, 2],
            hints=["0: Keep behind", "1: Take over", "2: Unfullscreen/unmaximize"],
            parent_ref="misc_menu_id",
            extended_help="**Focus Under Fullscreen**\n\nBehaviour when a window is focused while another is fullscreen:\n- 0 = Do nothing (new window stays behind)\n- 1 = Focused window takes over\n- 2 = Exit fullscreen/maximized state"
        ),
        ConfigItem(
            label="Workspace Tracking",
            key="initial_workspace_tracking",
            scope="misc",
            type_="int",
            default=1,
            options=[0, 1, 2],
            hints=["0: Disabled", "1: Single-shot", "2: Persistent (children too)"],
            parent_ref="misc_menu_id",
            extended_help="**Workspace Tracking**\n\nForces new windows to spawn on the workspace they were invoked from:\n- 0 = Disabled\n- 1 = Single-shot tracking of the invocation workspace\n- 2 = Persistent tracking, including child windows"
        ),
        ConfigItem(
            label="Navigation & Binds",
            key="binds_menu_id",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=False,
            group="System Behavior"
        ),
        ConfigItem(
            label="Allow Pin Fullscreen",
            key="allow_pin_fullscreen",
            scope="binds",
            type_="bool",
            default=True,
            parent_ref="binds_menu_id",
            extended_help="**Allow Pin Fullscreen**\n\nAllow pinned floating windows to transition into fullscreen mode."
        ),
        ConfigItem(
            label="Workspace Back and Forth",
            key="workspace_back_and_forth",
            scope="binds",
            type_="int",
            default=0,
            options=[0, 1, 2],
            hints=["0: Disabled", "1: All monitors", "2: Per monitor"],
            parent_ref="binds_menu_id",
            extended_help="**Back and Forth**\n\nRe-dispatching to the active workspace switches back to the previously active one."
        ),
        ConfigItem(
            label="Allow Workspace Cycles",
            key="allow_workspace_cycles",
            scope="binds",
            type_="bool",
            default=False,
            parent_ref="binds_menu_id",
            extended_help="**Allow Cycles**\n\nWorkspaces remember their previous workspace, allowing repeated previous-workspace navigation to form cycles."
        ),
        ConfigItem(
            label="Workspace Center On",
            key="workspace_center_on",
            scope="binds",
            type_="int",
            default=0,
            options=[0, 1],
            hints=["0: Workspace center", "1: Last active window"],
            parent_ref="binds_menu_id",
            extended_help="**Workspace Center On**\n\nWhere to center the cursor on a workspace switch: 0 centers on the workspace, 1 on its last active window. Cursor warp settings determine whether a warp occurs."
        ),
        ConfigItem(
            label="Hide Special on Change",
            key="hide_special_on_workspace_change",
            scope="binds",
            type_="bool",
            default=False,
            parent_ref="binds_menu_id",
            extended_help="**Hide Special on Change**\n\nHide open special workspaces (scratchpads) when you switch to a different normal workspace."
        ),
        ConfigItem(
            label="Movefocus Cycles Fullscreen",
            key="movefocus_cycles_fullscreen",
            scope="binds",
            type_="bool",
            default=True,
            parent_ref="binds_menu_id",
            extended_help="**Movefocus Cycles Fullscreen**\n\nAllow 'movefocus' to wrap around into/out of fullscreen windows."
        ),
        ConfigItem(
            label="Monitor Edge Fallback",
            key="window_direction_monitor_fallback",
            scope="binds",
            type_="bool",
            default=True,
            parent_ref="binds_menu_id",
            extended_help="**Monitor Fallback**\n\nMoving a window past the edge of a monitor moves it to the adjacent monitor."
        ),
        ConfigItem(
            label='Directional Focus Preference',
            key='focus_preferred_method',
            scope='binds',
            type_='int',
            default=0,
            options=[0, 1],
            parent_ref='binds_menu_id',
            extended_help='0 most recently used window, 1 longest shared edge.'
        ),
        ConfigItem(
            label='Cycle Group Tabs Before Leaving',
            key='movefocus_cycles_groupfirst',
            scope='binds',
            type_='bool',
            default=False,
            parent_ref='binds_menu_id',
            extended_help='Visit group tabs first during directional focus, then other windows.'
        ),
        ConfigItem(
            label='Dispatchers Ignore Group Lock',
            key='ignore_group_lock',
            scope='binds',
            type_='bool',
            default=False,
            parent_ref='binds_menu_id',
            extended_help='Allow group movement dispatchers to ignore individual group locks.'
        ),
        ConfigItem(
            label='Workspace Tracking Timeout',
            key='initial_workspace_token_timeout',
            scope='misc',
            type_='int',
            default=10,
            min_val=1,
            max_val=3600,
            step=1,
            parent_ref='misc_menu_id',
            extended_help='Seconds an invoked window has to open before its workspace token expires.'
        ),
        ConfigItem(
            label='Retain Fullscreen After Close',
            key='exit_window_retains_fullscreen',
            scope='misc',
            type_='int',
            default=0,
            options=[0, 1, 2, 3],
            parent_ref='misc_menu_id',
            extended_help='0 disabled, 1 enabled, 2 only grouped, 3 only ungrouped windows.'
        ),
        ConfigItem(
            label='Keep Floating Windows Onscreen',
            key='float_force_onscreen',
            scope='misc',
            type_='int',
            default=0,
            options=[0, 1, 2],
            parent_ref='misc_menu_id',
            extended_help='0 unconstrained, 1 partially visible, 2 fully visible.'
        ),
        ConfigItem(
            label='New Floating Windows Onscreen',
            key='new_float_force_onscreen',
            scope='misc',
            type_='int',
            default=2,
            options=[0, 1, 2],
            parent_ref='misc_menu_id',
            extended_help='0 unconstrained, 1 partially visible, 2 fully visible.'
        ),
        ConfigItem(
            label='Respect Tiled Window Size Limits',
            key='size_limits_tiled',
            scope='misc',
            type_='bool',
            default=False,
            parent_ref='misc_menu_id',
            extended_help='Apply min_size and max_size rules to tiled windows.'
        ),
        ConfigItem(
            label='Block Modal Parent Input',
            key='modal_parent_blocking',
            scope='general',
            type_='bool',
            default=True,
            group='General Layout',
            extended_help='Prevent interaction with a parent window while its modal dialog is open.'
        ),
        ConfigItem(
            label='Disable Empty Direction Focus Fallback',
            key='no_focus_fallback',
            scope='general',
            type_='bool',
            default=False,
            group='General Layout',
            extended_help='Do not focus another candidate when directional focus finds no window.'
        ),
        ConfigItem(
            label='Single Window Aspect Width',
            key='1',
            scope='layout/single_window_aspect_ratio',
            type_='float',
            default=0.0,
            group='Single Window Geometry',
            min_val=0,
            max_val=1000,
            step=1,
            extended_help='Aspect ratio for a lone window, e.g. 4 and 3. Zero disables extra aspect padding.'
        ),
        ConfigItem(
            label='Single Window Aspect Height',
            key='2',
            scope='layout/single_window_aspect_ratio',
            type_='float',
            default=0.0,
            group='Single Window Geometry',
            min_val=0,
            max_val=1000,
            step=1,
            extended_help='Aspect ratio for a lone window, e.g. 4 and 3. Zero disables extra aspect padding.'
        ),
        ConfigItem(
            label='Aspect Padding Tolerance',
            key='single_window_aspect_ratio_tolerance',
            scope='layout',
            type_='float',
            default=0.1,
            group='Single Window Geometry',
            min_val=0,
            max_val=1,
            step=0.01,
            extended_help='Ignore aspect padding smaller than this fraction of screen width or height.'
        ),
        ConfigItem(
            label='Automatically Group New Windows',
            key='auto_group',
            scope='group',
            type_='bool',
            default=True,
            group='Window Group Behavior',
            extended_help='New windows join the focused unlocked group.'
        ),
        ConfigItem(
            label='Drag Into Groups',
            key='drag_into_group',
            scope='group',
            type_='int',
            default=1,
            group='Window Group Behavior',
            options=[0, 1, 2],
            extended_help='0 disabled, 1 enabled, 2 only through the groupbar.'
        ),
        ConfigItem(
            label='Group on Workspace Move',
            key='group_on_movetoworkspace',
            scope='group',
            type_='bool',
            default=False,
            group='Window Group Behavior',
            extended_help='Moving a window into a workspace merges it with a solitary unlocked group.'
        ),
        ConfigItem(
            label='Insert Group Tab After Current',
            key='insert_after_current',
            scope='group',
            type_='bool',
            default=True,
            group='Window Group Behavior',
            extended_help='Insert new group tabs after the active tab instead of at the end.'
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: WORKSPACES (Granular Settings Injected Dynamically)
    # -------------------------------------------------------------------------
    1: WORKSPACE_ITEMS,

    # -------------------------------------------------------------------------
    # TAB 2: SCROLLING LAYOUT
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Direction",
            key="direction",
            scope="scrolling",
            type_="cycle",
            default="right",
            options=["right", "left", "down", "up"],
            group="Scrolling Layout Settings",
            extended_help="**Direction**\n\nThe direction in which new windows appear and the entire tape layout scrolls."
        ),
        ConfigItem(
            label="Focus Alignment",
            key="focus_fit_method",
            scope="scrolling",
            type_="int",
            default=0,
            options=[0, 1],
            hints=["0: Center (preview both sides)", "1: Fit (preview right only)"],
            group="Scrolling Layout Settings",
            extended_help="**Focus Alignment (focus_fit_method)**\n\nControls how the active column is positioned on screen when brought into view:\n\n- **0: Center (Both Sides Preview)**: Centers the focused column directly in the middle of the monitor. When `column_width` is less than `1.0` (such as `0.9` or `0.85`), adjacent windows peek in from **both** the left and right edges, providing a balanced preview of previous and next columns.\n- **1: Fit (Trailing Preview Only)**: Scrolls just enough to fit the column flush against the screen edge. Leaves preview space only on the trailing side, hiding any preview on the leading side."
        ),
        ConfigItem(
            label="Column Width",
            key="column_width",
            scope="scrolling",
            type_="float",
            default=0.9,
            min_val=0.1,
            max_val=1.0,
            step=0.05,
            group="Scrolling Layout Settings",
            extended_help="**Column Width**\n\nThe default width of a new column as a percentage fraction of the screen (0.1 - 1.0). For example, 0.9 occupies 90% of screen width, leaving 5% peek margins on both left and right edges when Focus Alignment is set to Center."
        ),
        ConfigItem(
            label="Fullscreen Single Column",
            key="fullscreen_on_one_column",
            scope="scrolling",
            type_="bool",
            default=True,
            group="Scrolling Layout Settings",
            extended_help="**Fullscreen Single Column**\n\nWhen enabled, if a workspace contains only one column, that column automatically expands to fill the entire monitor."
        ),
        ConfigItem(
            label="Follow Focus",
            key="follow_focus",
            scope="scrolling",
            type_="bool",
            default=True,
            group="Scrolling Layout Settings",
            extended_help="**Follow Focus**\n\nWhen enabled, automatically scrolls the layout tape to bring a window into view when it receives focus."
        ),
        ConfigItem(
            label="Min Focus Visibility",
            key="follow_min_visible",
            scope="scrolling",
            type_="float",
            default=0.4,
            min_val=0.0,
            max_val=1.0,
            step=0.05,
            group="Scrolling Layout Settings",
            extended_help="**Min Focus Visibility (follow_min_visible)**\n\nMinimum visible fraction (0.0 - 1.0) of a window required for mouse hover to automatically scroll the layout.\n\nSetting this to `0.4` (default) prevents runaway scrolling loops when the cursor lingers on edge previews. Edge windows can always be focused and scrolled by clicking on them or using keybinds."
        ),
        ConfigItem(
            label="Width Breakpoints",
            key="explicit_column_widths",
            scope="scrolling",
            type_="string",
            default="0.333, 0.5, 0.667, 1.0",
            group="Scrolling Layout Settings",
            extended_help="**Width Breakpoints (explicit_column_widths)**\n\nA comma-separated list of preconfigured width breakpoints used when resizing columns via dispatchers (colresize +conf/-conf)."
        ),
        ConfigItem(
            label="Wrap Focus",
            key="wrap_focus",
            scope="scrolling",
            type_="bool",
            default=True,
            group="Scrolling Layout Settings",
            extended_help="**Wrap Focus**\n\nWhen enabled, focusing past the ends of the layout tape wraps around between the first and last columns."
        ),
        ConfigItem(
            label="Wrap Column Swap",
            key="wrap_swapcol",
            scope="scrolling",
            type_="bool",
            default=True,
            group="Scrolling Layout Settings",
            extended_help="**Wrap Column Swap**\n\nWhen enabled, swapping a column past the edge wraps it around to the opposite end of the tape."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: DWINDLE LAYOUT
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Force Split Direction",
            key="force_split",
            scope="dwindle",
            type_="int",
            default=0,
            options=[0, 1, 2],
            hints=["0: Follow direction", "1: Right/Down", "2: Left/Up"],
            group="Dwindle Layout Settings",
            extended_help="**Force Split**\n\n0 = Split follows mouse (last window direction)\n1 = Always split right/down\n2 = Always split left/up"
        ),
        ConfigItem(
            label="Preserve Split",
            key="preserve_split",
            scope="dwindle",
            type_="bool",
            default=True,
            group="Dwindle Layout Settings",
            extended_help="**Preserve Split**\n\nIf enabled, the split (side/top) will not change regardless of what happens to the container. *Required for togglesplit dispatcher to work correctly.*"
        ),
        ConfigItem(
            label="Smart Split",
            key="smart_split",
            scope="dwindle",
            type_="bool",
            default=False,
            group="Dwindle Layout Settings",
            extended_help="**Smart Split**\n\nIf enabled, allows a more precise control over the window split direction based on the cursor's position within conceptual triangles."
        ),
        ConfigItem(
            label="Smart Resizing",
            key="smart_resizing",
            scope="dwindle",
            type_="bool",
            default=True,
            group="Dwindle Layout Settings",
            extended_help="**Smart Resizing**\n\nIf enabled, resizing direction will be determined by the mouse's position on the window (nearest to which corner). Else, it relies purely on tiling position."
        ),
        ConfigItem(
            label="Permanent Direction Override",
            key="permanent_direction_override",
            scope="dwindle",
            type_="bool",
            default=False,
            group="Dwindle Layout Settings",
            extended_help="**Permanent Direction Override**\n\nIf enabled, makes a preselected direction persist until turned off or a non-direction is specified."
        ),
        ConfigItem(
            label="Special Workspace Scale",
            key="special_scale_factor",
            scope="dwindle",
            type_="float",
            default=1.0,
            min_val=0.1,
            max_val=1.0,
            step=0.1,
            group="Dwindle Layout Settings",
            extended_help="**Special Scale Factor**\n\nScale factor for windows located on special workspaces (scratchpads)."
        ),
        ConfigItem(
            label="Split Width Multiplier",
            key="split_width_multiplier",
            scope="dwindle",
            type_="float",
            default=1.0,
            min_val=0.5,
            max_val=3.0,
            step=0.1,
            group="Dwindle Layout Settings",
            extended_help="**Split Width Multiplier**\n\nUseful for ultrawide monitors where a window's width remains greater than its height even after multiple splits."
        ),
        ConfigItem(
            label="Use Active For Splits",
            key="use_active_for_splits",
            scope="dwindle",
            type_="bool",
            default=True,
            group="Dwindle Layout Settings",
            extended_help="**Use Active For Splits**\n\nWhether to prefer the active window or the mouse position when calculating splits."
        ),
        ConfigItem(
            label="Default Split Ratio",
            key="default_split_ratio",
            scope="dwindle",
            type_="float",
            default=1.0,
            min_val=0.1,
            max_val=1.9,
            step=0.1,
            group="Dwindle Layout Settings",
            extended_help="**Default Split Ratio**\n\nThe ratio on window open. 1.0 means an even 50/50 split."
        ),
        ConfigItem(
            label="Split Bias",
            key="split_bias",
            scope="dwindle",
            type_="int",
            default=0,
            options=[0, 1],
            hints=["0: Directional (top/left)", "1: Active window"],
            group="Dwindle Layout Settings",
            extended_help="**Split Bias**\n\nSpecifies which window receives the split ratio:\n- 0 = Directional (the top or left window)\n- 1 = The current active window"
        ),
        ConfigItem(
            label="Precise Mouse Move",
            key="precise_mouse_move",
            scope="dwindle",
            type_="bool",
            default=False,
            group="Dwindle Layout Settings",
            extended_help="**Precise Mouse Move**\n\nWhen using the bindm 'movewindow' dispatcher, this will drop the window more precisely depending on the exact mouse coordinates."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: MASTER LAYOUT
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="New Window Status",
            key="new_status",
            scope="master",
            type_="cycle",
            default="slave",
            options=["slave", "master", "inherit"],
            group="Master Layout Settings",
            extended_help="**New Status**\n\n- `slave`: Go to the slave stack (default)\n- `master`: New windows always become the master\n- `inherit`: Inherit status of the focused window"
        ),
        ConfigItem(
            label="New Windows on Top",
            key="new_on_top",
            scope="master",
            type_="bool",
            default=False,
            group="Master Layout Settings",
            extended_help="**New on Top**\n\nInsert new windows at the TOP of the stack instead of the bottom."
        ),
        ConfigItem(
            label="New on Active",
            key="new_on_active",
            scope="master",
            type_="cycle",
            default="none",
            options=["none", "before", "after"],
            group="Master Layout Settings",
            extended_help="**New on Active**\n\nPlace new windows relative to the currently focused window (`before` or `after`). If `none`, behaves according to 'New Windows on Top'."
        ),
        ConfigItem(
            label="Orientation",
            key="orientation",
            scope="master",
            type_="cycle",
            default="left",
            options=["left", "right", "top", "bottom", "center"],
            group="Master Layout Settings",
            extended_help="**Orientation**\n\nControls which side the master pane occupies. 'center' uses a central master with stacks on both left and right sides."
        ),
        ConfigItem(
            label="Master Size Fraction",
            key="mfact",
            scope="master",
            type_="float",
            default=0.55,
            min_val=0.10,
            max_val=0.90,
            step=0.05,
            group="Master Layout Settings",
            extended_help="**Master Fraction (mfact)**\n\nThe percentage fraction (0.1 - 0.9) of the screen the master pane occupies."
        ),
        ConfigItem(
            label="Allow Small Split",
            key="allow_small_split",
            scope="master",
            type_="bool",
            default=False,
            group="Master Layout Settings",
            extended_help="**Allow Small Split**\n\nAllow adding extra master windows in horizontal-split style when there are multiple masters."
        ),
        ConfigItem(
            label="Slave Count For Center",
            key="slave_count_for_center_master",
            scope="master",
            type_="int",
            default=2,
            min_val=0,
            max_val=10,
            step=1,
            group="Master Layout Settings",
            extended_help="**Slave Count For Center**\n\nWhen using orientation=center, the master window is only centered when at least this many slave windows are open. Set to 0 to always center."
        ),
        ConfigItem(
            label="Center Master Fallback",
            key="center_master_fallback",
            scope="master",
            type_="cycle",
            default="left",
            options=["left", "right", "top", "bottom"],
            group="Master Layout Settings",
            extended_help="**Center Master Fallback**\n\nThe orientation to use when the slave count is lower than the required threshold for centering."
        ),
        ConfigItem(
            label="Center Ignores Reserved",
            key="center_ignores_reserved",
            scope="master",
            type_="bool",
            default=False,
            group="Master Layout Settings",
            extended_help="**Center Ignores Reserved**\n\nCenters the master window on the monitor ignoring reserved areas (such as top and bottom status bars)."
        ),
        ConfigItem(
            label="Smart Resizing",
            key="smart_resizing",
            scope="master",
            type_="bool",
            default=True,
            group="Master Layout Settings",
            extended_help="**Smart Resizing**\n\nResizing direction determined by nearest corner to mouse position."
        ),
        ConfigItem(
            label="Drop at Cursor",
            key="drop_at_cursor",
            scope="master",
            type_="bool",
            default=True,
            group="Master Layout Settings",
            extended_help="**Drop at Cursor**\n\nDragging and dropping windows puts them at the exact cursor position rather than the ends of the stack."
        ),
        ConfigItem(
            label="Always Keep Position",
            key="always_keep_position",
            scope="master",
            type_="bool",
            default=False,
            group="Master Layout Settings",
            extended_help="**Always Keep Position**\n\nKeeps the master window locked in its configured position even when there are absolutely no slave windows open."
        ),
        ConfigItem(
            label="Focus Master on Close",
            key="focus_master_on_close",
            scope="master",
            type_="bool",
            default=False,
            group="Master Layout Settings",
            extended_help="**Focus Master on Close**\n\nWhen enabled, closing any window automatically moves focus to the master window."
        ),
        ConfigItem(
            label="Special Workspace Scale",
            key="special_scale_factor",
            scope="master",
            type_="float",
            default=1.0,
            min_val=0.1,
            max_val=1.0,
            step=0.1,
            group="Master Layout Settings",
            extended_help="**Special Scale Factor**\n\nScale factor for windows on special (scratchpad) workspaces when using the master layout."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 5: PRESETS
    # -------------------------------------------------------------------------
    5: [
        ConfigItem(
            label="Strict Focus Profile",
            key="preset_strict_focus",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Profiles & Actions",
            preset_payload={
                "misc.on_focus_under_fullscreen": 2,
                "misc.initial_workspace_tracking": 1,
                "misc.focus_on_activate": True
            },
            extended_help="**Strict Focus**\n\nApplies recommended behavior: popups and new windows drop fullscreen apps to reveal the newly focused window immediately."
        ),
        ConfigItem(
            label="Immersive/Do Not Disturb Profile",
            key="preset_immersive_focus",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Profiles & Actions",
            preset_payload={
                "misc.on_focus_under_fullscreen": 0,
                "misc.focus_on_activate": False
            },
            extended_help="**Immersive Profile**\n\nPrevents any background application from stealing focus or dropping your current fullscreen application."
        ),
        ConfigItem(
            label="Balanced Scrolling Profile",
            key="preset_scrolling_centered",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Profiles & Actions",
            preset_payload={
                "scrolling.focus_fit_method": 0,
                "scrolling.column_width": 0.9,
                "scrolling.fullscreen_on_one_column": True,
                "scrolling.follow_focus": True,
                "scrolling.follow_min_visible": 0.4
            },
            extended_help="**Balanced Scrolling Profile**\n\nApplies centered focus alignment (previews visible on both left and right edges) with 90% column width for balanced scrolling workspaces."
        ),
        ConfigItem(
            label="Reload Window Rules",
            key="action_reload_hypr",
            scope="DEFAULT",
            type_="action",
            default="hyprctl reload",
            group="Profiles & Actions",
            extended_help="**Reload Environment**\n\nForces Hyprland to re-read all window rules and configuration files without terminating the session."
        ),
    ]
}

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
