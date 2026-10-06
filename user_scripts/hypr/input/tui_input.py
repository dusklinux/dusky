#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: INPUT CONFIGURATION SCHEMA
===============================================================================
Target: ~/.config/hypr/edit_here/source/input.lua
Engine: LUA AST Mapper
===============================================================================
"""

import sys
from pathlib import Path

_dusky_root = Path.home() / "user_scripts" / "dusky_tui"
if str(_dusky_root) not in sys.path:
    sys.path.insert(0, str(_dusky_root))

import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "lua"
TARGET_FILE = "~/.config/hypr/edit_here/source/input.lua"
APP_TITLE = "Dusky Input Configuration"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Profiles"

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Pointer",
    "Touch",
    "Keyboard",
    "Profiles",
    "Cursor",
    "Tablet"
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: POINTER (Mouse & Cursor)
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Mouse Sensitivity",
            key="sensitivity",
            scope="input",
            type_="float",
            default=0.0,
            min_val=-1.0,
            max_val=1.0,
            step=0.1,
            group="Sensor",
            extended_help="**Sensitivity**\n\nSets the mouse input sensitivity. Value is clamped to the range -1.0 to 1.0. 0.0 is neutral/default."
        ),
        ConfigItem(
            label="Acceleration Profile",
            key="accel_profile",
            scope="input",
            type_="cycle",
            default="adaptive",
            options=["adaptive", "flat", "custom"],
            group="Sensor",
            extended_help="**Acceleration Profile**\n\nSets the cursor acceleration profile.\n- **Adaptive**: Accelerates naturally based on speed.\n- **Flat**: Constant sensitivity regardless of speed (good for gaming).\n- **Custom**: Based on scroll points."
        ),
        ConfigItem(
            label="Force No Acceleration",
            key="force_no_accel",
            scope="input",
            type_="bool",
            default=False,
            group="Sensor",
            extended_help="**Force No Acceleration**\n\nBypasses all acceleration curves to grab the raw signal only. Warning: Can cause cursor desynchronization; 'flat' profile is generally preferred for gaming."
        ),
        ConfigItem(
            label="Left Handed Mode",
            key="left_handed",
            scope="input",
            type_="bool",
            default=False,
            group="Sensor",
            extended_help="**Left Handed**\n\nSwitches the Right Mouse Button (RMB) and Left Mouse Button (LMB) across devices."
        ),
        ConfigItem(
            label="Natural Scrolling",
            key="natural_scroll",
            scope="input",
            type_="bool",
            default=False,
            group="Scrolling",
            extended_help="**Natural Scrolling**\n\nInverts scrolling direction for standard mice. Scrolling down moves content up, similar to touchscreen behavior."
        ),
        ConfigItem(
            label="Scroll Method",
            key="scroll_method",
            scope="input",
            type_="cycle",
            default="2fg",
            options=["2fg", "edge", "on_button_down", "no_scroll"],
            group="Scrolling",
            extended_help="**Scroll Method**\n\nDetermines the input method required to trigger scroll events (e.g., Two-Finger '2fg' or edge scrolling)."
        ),
        ConfigItem(
            label="Mouse Scroll Factor",
            key="scroll_factor",
            scope="input",
            type_="float",
            default=1.0,
            min_val=0.1,
            max_val=5.0,
            step=0.1,
            group="Scrolling",
            extended_help="**Scroll Factor**\n\nMultiplier added to scroll movement for external mice. Higher values make the scroll wheel move content faster."
        ),
        ConfigItem(
            label="Window Focus Behavior",
            key="follow_mouse",
            scope="input",
            type_="int",
            default=1,
            options=[0, 1, 2, 3],
            is_parent=True,
            expanded=False,
            group="Focus",
            extended_help="**Follow Mouse**\n\nSpecify if and how cursor movement affects window focus.\n- 0: Cursor won't focus windows.\n- 1: Cursor focuses windows on hover.\n- 2/3: Advanced click-to-focus hybrid behaviors."
        ),
        ConfigItem(
            label="Focus Deadzone Shrink",
            key="follow_mouse_shrink",
            scope="input",
            type_="int",
            default=0,
            min_val=0,
            max_val=300,
            step=1,
            parent_ref="input.follow_mouse",
            extended_help="**Focus Hitbox Shrink**\n\nShrinks the inactive window hitboxes used for focus detection by the specified pixels. Creates a dead zone in gaps where moving the cursor won't change focus (Only applies if Follow Mouse is 1)."
        ),
        ConfigItem(
            label="Mouse Refocus",
            key="mouse_refocus",
            scope="input",
            type_="bool",
            default=True,
            group="Focus",
            extended_help="**Mouse Refocus**\n\nIf disabled, mouse focus won't automatically switch unless crossing a window boundary when follow_mouse is set to 1."
        ),
        ConfigItem(
            label="Float Switch Override Focus",
            key="float_switch_override_focus",
            scope="input",
            type_="int",
            default=1,
            options=[0, 1, 2],
            group="Focus",
            extended_help="**Float Switch Override**\n\nFocus behavior when changing tiled-to-floating.\n- 0: Disabled.\n- 1: Focus changes to window under cursor on tiled/float swap.\n- 2: Focus also follows mouse on float-to-float switches."
        ),
        ConfigItem(
            label='Pointer Rotation',
            key='rotation',
            scope='input',
            type_='int',
            default=0,
            group='Sensor',
            min_val=0,
            max_val=359,
            step=1,
            extended_help='Clockwise rotation in degrees for pointer devices.'
        ),
        ConfigItem(
            label='Scroll Button',
            key='scroll_button',
            scope='input',
            type_='int',
            default=0,
            group='Scrolling',
            min_val=0,
            max_val=300,
            step=1,
            extended_help='Button code for on_button_down scrolling; 0 uses the device default.'
        ),
        ConfigItem(
            label='Latch Scroll Button',
            key='scroll_button_lock',
            scope='input',
            type_='bool',
            default=False,
            group='Scrolling',
            extended_help='Toggle scrolling with a press/release instead of holding the scroll button.'
        ),
        ConfigItem(
            label='Discrete Scroll Emulation',
            key='emulate_discrete_scroll',
            scope='input',
            type_='int',
            default=1,
            group='Scrolling',
            options=[0, 1, 2],
            extended_help='0 disables emulation, 1 handles nonstandard events, 2 handles all scroll events.'
        ),
        ConfigItem(
            label='Custom Scroll Curve',
            key='scroll_points',
            scope='input',
            type_='string',
            default='',
            group='Scrolling',
            extended_help='Custom acceleration profile: <step> <points>. Used with custom acceleration; empty gives a flat scroll curve.'
        ),
        ConfigItem(
            label='Focus Movement Threshold',
            key='follow_mouse_threshold',
            scope='input',
            type_='float',
            default=0.0,
            group='Focus',
            min_val=0,
            max_val=100,
            step=0.5,
            extended_help='Minimum logical pixel movement before hover focus changes; applies when Follow Mouse is 1.'
        ),
        ConfigItem(
            label='Focus After Close',
            key='focus_on_close',
            scope='input',
            type_='int',
            default=0,
            group='Focus',
            options=[0, 1, 2],
            extended_help='0 next candidate, 1 under cursor, 2 most recently active window.'
        ),
        ConfigItem(
            label='Focus Through Floating Scratchpad',
            key='special_fallthrough',
            scope='input',
            type_='bool',
            default=False,
            group='Focus',
            extended_help='Allow focusing regular workspace windows when a special workspace contains only floating windows.'
        ),
        ConfigItem(
            label='Scroll Outside Focused Window',
            key='off_window_axis_events',
            scope='input',
            type_='int',
            default=1,
            group='Scrolling',
            options=[0, 1, 2, 3],
            extended_help='0 ignore, 1 send out-of-bounds coordinates, 2 clamp to window, 3 warp cursor into window.'
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: TOUCH (Touchpad & Devices)
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Disable Touchpad While Typing",
            key="disable_while_typing",
            scope="input/touchpad",
            type_="bool",
            default=True,
            group="Touchpad",
            extended_help="**Typing Protection**\n\nAutomatically disables touchpad input while the keyboard is actively in use to prevent accidental palm clicks."
        ),
        ConfigItem(
            label="Touchpad Natural Scroll",
            key="natural_scroll",
            scope="input/touchpad",
            type_="bool",
            default=True,
            group="Touchpad",
            extended_help="**Touchpad Natural Scrolling**\n\nInverts vertical touchpad scrolling direction so that scrolling moves the page content directly."
        ),
        ConfigItem(
            label="Touchpad Scroll Factor",
            key="scroll_factor",
            scope="input/touchpad",
            type_="float",
            default=1.0,
            min_val=0.1,
            max_val=5.0,
            step=0.1,
            group="Touchpad",
            extended_help="**Scroll Factor**\n\nMultiplier applied to the amount of scroll movement specifically for the touchpad."
        ),
        ConfigItem(
            label="Tap to Click",
            key="tap_to_click",
            scope="input/touchpad",
            type_="bool",
            default=True,
            group="Touchpad",
            extended_help="**Tap to Click**\n\nAllows tapping on the touchpad surface to register as a mouse click (1 finger = Left, 2 fingers = Right, 3 fingers = Middle)."
        ),
        ConfigItem(
            label="Clickfinger Behavior",
            key="clickfinger_behavior",
            scope="input/touchpad",
            type_="bool",
            default=False,
            group="Touchpad",
            extended_help="**Clickfinger Behavior**\n\nChanges physical button presses based on the number of fingers touching the pad (e.g., 2 fingers down + click = Right Click) instead of relying on click-pad zones."
        ),
        ConfigItem(
            label="Middle Button Emulation",
            key="middle_button_emulation",
            scope="input/touchpad",
            type_="bool",
            default=False,
            group="Touchpad",
            extended_help="**Middle Button Emulation**\n\nSending Left Mouse Button and Right Mouse Button simultaneously will be interpreted as a middle click."
        ),
        ConfigItem(
            label="Tap and Drag",
            key="tap_and_drag",
            scope="input/touchpad",
            type_="bool",
            default=True,
            is_parent=True,
            expanded=False,
            group="Touchpad",
            extended_help="**Tap and Drag**\n\nEnables tap-and-drag mode for the touchpad. Double tap and hold the second tap to drag."
        ),
        ConfigItem(
            label="Drag Lock Behavior",
            key="drag_lock",
            scope="input/touchpad",
            type_="int",
            default=0,
            options=[0, 1, 2],
            parent_ref="input/touchpad.tap_and_drag",
            extended_help="**Drag Lock**\n\nWhen enabled, lifting the finger off while dragging will not drop the dragged item.\n- 0: Disabled\n- 1: Enabled with timeout\n- 2: Enabled strictly (sticky)"
        ),
        ConfigItem(
            label="Enable Touchscreen Device",
            key="enabled",
            scope="input/touchdevice",
            type_="bool",
            default=True,
            group="Touchscreen",
            extended_help="**Enable Touchscreen**\n\nGlobally enables or disables direct touchscreen inputs on your displays."
        ),
        ConfigItem(
            label='Tap Button Mapping',
            key='tap_button_map',
            scope='input/touchpad',
            type_='cycle',
            default='lrm',
            group='Touchpad',
            options=['lrm', 'lmr'],
            extended_help='lrm maps 1/2/3 fingers to left/right/middle; lmr maps them to left/middle/right.'
        ),
        ConfigItem(
            label='Three/Four Finger Drag',
            key='drag_3fg',
            scope='input/touchpad',
            type_='int',
            default=0,
            group='Touchpad',
            options=[0, 1, 2],
            extended_help='0 disabled, 1 three-finger drag, 2 four-finger drag. Can compete with gestures using the same fingers.'
        ),
        ConfigItem(
            label='Invert Touchpad X',
            key='flip_x',
            scope='input/touchpad',
            type_='bool',
            default=False,
            group='Touchpad',
            extended_help='Invert horizontal touchpad movement.'
        ),
        ConfigItem(
            label='Invert Touchpad Y',
            key='flip_y',
            scope='input/touchpad',
            type_='bool',
            default=False,
            group='Touchpad',
            extended_help='Invert vertical touchpad movement.'
        ),
        ConfigItem(
            label='Touchscreen Transform',
            key='transform',
            scope='input/touchdevice',
            type_='int',
            default=0,
            group='Touchscreen',
            min_val=0,
            max_val=6,
            step=1,
            extended_help='Wayland input transform: 0 normal, 1/2/3 rotation by 90/180/270 degrees, 4 flipped, 5 flipped 90, 6 flipped 180. This build accepts 0–6.'
        ),
        ConfigItem(
            label='Touchscreen Output',
            key='output',
            scope='input/touchdevice',
            type_='string',
            default='[[Auto]]',
            group='Touchscreen',
            extended_help='Output name or description to map touch input; [[Auto]] enables compositor mapping.'
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: KEYBOARD
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Auto-Detect System Layout",
            key="action_autodetect_kb",
            scope="DEFAULT",
            type_="action",
            default="bash ~/.config/hypr/scripts/025_configure_keyboard.sh",
            group="Layout",
            extended_help="**Auto-Detect Layout**\n\nExecutes the `025_configure_keyboard.sh` script to automatically detect your system's locale (via localectl) and patch the keyboard layout seamlessly."
        ),
        ConfigItem(
            label="Keyboard Layout (Presets)",
            key="kb_layout",
            scope="input",
            type_="string",
            default="us",
            options=["us", "gb", "de", "fr", "es", "it", "cz", "pt", "ch", "ru", "pl", "jp", "fi", "dk", "no", "se", "br", "latam"],
            group="Layout",
            extended_help="**Keyboard Layout**\n\nSets the primary XKB keymap layout parameter. Controls the base language for your keyboard. You can select from the presets list or manually type a custom layout."
        ),
        ConfigItem(
            label="Keyboard Variant",
            key="kb_variant",
            scope="input",
            type_="string",
            default="",
            group="Layout",
            extended_help="**Keyboard Variant**\n\nSets the XKB keymap variant. Leave blank for standard layouts, or specify variants like 'intl' for international configurations."
        ),
        ConfigItem(
            label="Keyboard Options",
            key="kb_options",
            scope="input",
            type_="string",
            default="",
            group="Layout",
            extended_help="**Keyboard Options**\n\nSets XKB options, such as swapping Caps Lock and Escape. Example: 'caps:escape' or 'ctrl:nocaps'."
        ),
        ConfigItem(
            label="Repeat Rate",
            key="repeat_rate",
            scope="input",
            type_="int",
            default=35,
            min_val=0,
            max_val=200,
            step=5,
            group="Behavior",
            extended_help="**Repeat Rate**\n\nThe rate at which held-down keys repeat, measured in repeats per second. Higher values make the cursor or character repeat faster when holding a key."
        ),
        ConfigItem(
            label="Repeat Delay",
            key="repeat_delay",
            scope="input",
            type_="int",
            default=250,
            min_val=0,
            max_val=2000,
            step=50,
            group="Behavior",
            extended_help="**Repeat Delay**\n\nThe delay before a held-down key starts repeating, in milliseconds. Lower values make repeat behavior kick in faster."
        ),
        ConfigItem(
            label="Enable Numlock by Default",
            key="numlock_by_default",
            scope="input",
            type_="bool",
            default=False,
            group="Behavior",
            extended_help="**Numlock Default**\n\nIf enabled, the numpad will automatically be active (Numlock engaged) when the compositor starts up."
        ),
        ConfigItem(
            label="Resolve Binds by Symbol",
            key="resolve_binds_by_sym",
            scope="input",
            type_="bool",
            default=False,
            group="Behavior",
            extended_help="**Resolve Binds by Symbol**\n\nDetermines how keybinds act when multiple layouts are used. If enabled, keybinds specified by symbols are activated when you type the respective symbol with the current layout."
        ),
        ConfigItem(
            label='Keyboard Model',
            key='kb_model',
            scope='input',
            type_='string',
            default='',
            group='Layout',
            extended_help='XKB kb_model setting; empty uses the normal layout path. A custom file takes precedence over layout parameters.'
        ),
        ConfigItem(
            label='Keyboard Rules',
            key='kb_rules',
            scope='input',
            type_='string',
            default='',
            group='Layout',
            extended_help='XKB kb_rules setting; empty uses the normal layout path. A custom file takes precedence over layout parameters.'
        ),
        ConfigItem(
            label='Custom XKB File',
            key='kb_file',
            scope='input',
            type_='string',
            default='',
            group='Layout',
            extended_help='XKB kb_file setting; empty uses the normal layout path. A custom file takes precedence over layout parameters.'
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: PROFILES (Presets)
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Apply Mac-Like Touch Profile",
            key="preset_mac_defaults",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System",
            preset_payload={
                "input/touchpad.natural_scroll": True,
                "input/touchpad.tap_to_click": True,
                "input/touchpad.clickfinger_behavior": True,
                "input.left_handed": False,
                "input.accel_profile": "adaptive"
            },
            extended_help="**Mac-Like Touch Defaults**\n\nApplies intuitive touchpad scrolling, tap-to-click, clickfinger behavior, and adaptive acceleration commonly found on macOS devices."
        ),
        ConfigItem(
            label="Apply Raw Gaming Input Profile",
            key="preset_raw_gaming",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System",
            preset_payload={
                "input.accel_profile": "flat",
                "input.force_no_accel": False,
                "input.sensitivity": 0.0,
                "cursor.no_hardware_cursors": 0,
                "input.left_handed": False
            },
            extended_help="**Raw Gaming Input**\n\nOptimizes mouse settings for FPS gaming by flattening the acceleration curve to ensure 1:1 raw mouse movement input without artificial acceleration."
        ),
    ],
    4: [
        ConfigItem(
            label='Disable Cursor Warps',
            key='no_warps',
            scope='cursor',
            type_='bool',
            default=False,
            group='Warping',
            extended_help='Suppress automatic cursor warps when changing focus.'
        ),
        ConfigItem(
            label='Remember Cursor Position',
            key='persistent_warps',
            scope='cursor',
            type_='bool',
            default=False,
            group='Warping',
            extended_help='Restore cursor position relative to a refocused window.'
        ),
        ConfigItem(
            label='Warp on Workspace Change',
            key='warp_on_change_workspace',
            scope='cursor',
            type_='int',
            default=0,
            group='Warping',
            options=[0, 1, 2],
            extended_help='0 disabled, 1 enabled, 2 force despite Disable Cursor Warps.'
        ),
        ConfigItem(
            label='Warp on Scratchpad Toggle',
            key='warp_on_toggle_special',
            scope='cursor',
            type_='int',
            default=0,
            group='Warping',
            options=[0, 1, 2],
            extended_help='0 disabled, 1 enabled, 2 force despite Disable Cursor Warps.'
        ),
        ConfigItem(
            label='Warp on Monitor Change',
            key='warp_on_monitor_change',
            scope='cursor',
            type_='int',
            default=-1,
            group='Warping',
            options=[-1, 0, 1, 2],
            extended_help='-1 follows workspace warp mode, 0 disabled, 1 enabled, 2 forced.'
        ),
        ConfigItem(
            label='Restore Cursor After Other Input',
            key='warp_back_after_non_mouse_input',
            scope='cursor',
            type_='bool',
            default=False,
            group='Warping',
            extended_help='Restore cursor position when returning to mouse input after another input device.'
        ),
        ConfigItem(
            label='Hide Cursor on Touch',
            key='hide_on_touch',
            scope='cursor',
            type_='bool',
            default=True,
            group='Rendering',
            extended_help='Hide the cursor until mouse input after using a touchscreen.'
        ),
        ConfigItem(
            label='Hide Cursor on Tablet Input',
            key='hide_on_tablet',
            scope='cursor',
            type_='bool',
            default=True,
            group='Rendering',
            extended_help='Hide the cursor until mouse input after using a tablet.'
        ),
        ConfigItem(
            label='Center Zoom on Cursor',
            key='zoom_rigid',
            scope='cursor',
            type_='bool',
            default=False,
            group='Zoom',
            extended_help='Keep the cursor centered when zooming instead of following loosely.'
        ),
        ConfigItem(
            label='Detached Zoom Camera',
            key='zoom_detached_camera',
            scope='cursor',
            type_='bool',
            default=True,
            group='Zoom',
            extended_help='Move the zoomed camera only when the pointer approaches its edge.'
        ),
        ConfigItem(
            label='Pixelated Zoom',
            key='zoom_disable_aa',
            scope='cursor',
            type_='bool',
            default=False,
            group='Zoom',
            extended_help='Disable antialiasing when zooming for crisp pixel edges.'
        ),
        ConfigItem(
            label='Sync Cursor Theme with GTK',
            key='sync_gsettings_theme',
            scope='cursor',
            type_='bool',
            default=True,
            group='Rendering',
            extended_help='Sync cursor theme and size to GSettings for GTK client decorations.'
        ),
        ConfigItem(
            label='Avoid Cursor VRR Frame Spikes',
            key='no_break_fs_vrr',
            scope='cursor',
            type_='int',
            default=2,
            group='Rendering',
            options=[0, 1, 2],
            extended_help='0 disabled, 1 enabled, 2 enabled for game content; controls scheduling on cursor movement.'
        ),
        ConfigItem(
            label='CPU Hardware Cursor Buffer',
            key='use_cpu_buffer',
            scope='cursor',
            type_='int',
            default=2,
            group='Rendering',
            options=[0, 1, 2],
            extended_help='0 disabled, 1 enabled, 2 automatic on NVIDIA. Useful for hardware cursor compatibility.'
        ),
        ConfigItem(
            label='Minimum Cursor Refresh Rate',
            key='min_refresh_rate',
            scope='cursor',
            type_='int',
            default=24,
            group='Rendering',
            min_val=10,
            max_val=500,
            step=1,
            extended_help='Minimum refresh rate while avoiding fullscreen VRR frame spikes; choose a rate supported by the display.'
        ),
        ConfigItem(
            label="Hide Cursor on Key Press",
            key="hide_on_key_press",
            scope="cursor",
            type_="bool",
            default=False,
            group="Cursor",
            extended_help="**Hide on Typing**\n\nAutomatically hides the mouse cursor when you press any keyboard key, preventing it from obscuring text. It reappears instantly upon moving the mouse."
        ),
        ConfigItem(
            label="Cursor Inactivity Timeout",
            key="inactive_timeout",
            scope="cursor",
            type_="float",
            default=0.0,
            min_val=0.0,
            max_val=20.0,
            step=1.0,
            group="Cursor",
            extended_help="**Inactivity Timeout**\n\nIn seconds, defines how long to wait during cursor inactivity before completely hiding it. Set to 0.0 to disable hiding."
        ),
        ConfigItem(
            label="Hardware Cursors",
            key="no_hardware_cursors",
            scope="cursor",
            type_="int",
            default=2,
            options=[0, 1, 2],
            group="Cursor",
            extended_help="**Hardware Cursors**\n\nControls hardware cursor usage.\n- 0: Force Hardware\n- 1: Force Software (Fixes invisible cursors on some Nvidia cards)\n- 2: Auto"
        ),
        ConfigItem(
            label="Cursor Zoom Factor",
            key="zoom_factor",
            scope="cursor",
            type_="float",
            default=1.0,
            min_val=1.0,
            max_val=10.0,
            step=0.1,
            group="Cursor",
            extended_help="**Cursor Zoom Factor**\n\nThe factor to zoom by around the cursor, functioning like a magnifying glass. Minimum 1.0 (meaning no zoom)."
        ),
        ConfigItem(
            label="Enable Hyprcursor Integration",
            key="enable_hyprcursor",
            scope="cursor",
            type_="bool",
            default=True,
            group="Cursor",
            extended_help="**Hyprcursor Support**\n\nToggles native rendering capabilities for advanced cursor themes using the Hyprcursor standard."
        ),
    ],
    5: [
        ConfigItem(
            label='Enable Tablet',
            key='enabled',
            scope='input/tablet',
            type_='bool',
            default=True,
            group='Mapping',
            extended_help='Enable tablet input.'
        ),
        ConfigItem(
            label='Tablet Transform',
            key='transform',
            scope='input/tablet',
            type_='int',
            default=0,
            group='Mapping',
            options=[0, 1, 2, 3, 4, 5, 6],
            extended_help='Normal/rotated/flipped Wayland transform; installed Git build accepts 0–6.'
        ),
        ConfigItem(
            label='Tablet Output',
            key='output',
            scope='input/tablet',
            type_='string',
            default='',
            group='Mapping',
            extended_help='Output name or description; empty maps across all monitors.'
        ),
        ConfigItem(
            label='Relative Tablet Input',
            key='relative_input',
            scope='input/tablet',
            type_='bool',
            default=False,
            group='Mapping',
            extended_help='Use relative tablet movement instead of absolute positioning.'
        ),
        ConfigItem(
            label='Left Handed Tablet',
            key='left_handed',
            scope='input/tablet',
            type_='bool',
            default=False,
            group='Mapping',
            extended_help='Rotate the tablet by 180 degrees.'
        ),
        ConfigItem(
            label='Absolute Mapping Position',
            key='absolute_region_position',
            scope='input/tablet',
            type_='bool',
            default=False,
            group='Mapping',
            extended_help='Treat the mapped region position as absolute monitor layout coordinates.'
        ),
        ConfigItem(
            label='Mapped Position X',
            key='1',
            scope='input/tablet/region_position',
            type_='float',
            default=0.0,
            group='Mapping',
            min_val=-20000,
            max_val=20000,
            step=1,
            extended_help='Mapping coordinates are layout pixels; active area coordinates are tablet millimeters. Zero size keeps the full default area.'
        ),
        ConfigItem(
            label='Mapped Position Y',
            key='2',
            scope='input/tablet/region_position',
            type_='float',
            default=0.0,
            group='Mapping',
            min_val=-20000,
            max_val=20000,
            step=1,
            extended_help='Mapping coordinates are layout pixels; active area coordinates are tablet millimeters. Zero size keeps the full default area.'
        ),
        ConfigItem(
            label='Mapped Size X',
            key='1',
            scope='input/tablet/region_size',
            type_='float',
            default=0.0,
            group='Mapping',
            min_val=-100,
            max_val=4000,
            step=1,
            extended_help='Mapping coordinates are layout pixels; active area coordinates are tablet millimeters. Zero size keeps the full default area.'
        ),
        ConfigItem(
            label='Mapped Size Y',
            key='2',
            scope='input/tablet/region_size',
            type_='float',
            default=0.0,
            group='Mapping',
            min_val=-100,
            max_val=4000,
            step=1,
            extended_help='Mapping coordinates are layout pixels; active area coordinates are tablet millimeters. Zero size keeps the full default area.'
        ),
        ConfigItem(
            label='Active Area Position X',
            key='1',
            scope='input/tablet/active_area_position',
            type_='float',
            default=0.0,
            group='Mapping',
            min_val=0,
            max_val=500,
            step=1,
            extended_help='Mapping coordinates are layout pixels; active area coordinates are tablet millimeters. Zero size keeps the full default area.'
        ),
        ConfigItem(
            label='Active Area Position Y',
            key='2',
            scope='input/tablet/active_area_position',
            type_='float',
            default=0.0,
            group='Mapping',
            min_val=0,
            max_val=500,
            step=1,
            extended_help='Mapping coordinates are layout pixels; active area coordinates are tablet millimeters. Zero size keeps the full default area.'
        ),
        ConfigItem(
            label='Active Area Size X',
            key='1',
            scope='input/tablet/active_area_size',
            type_='float',
            default=0.0,
            group='Mapping',
            min_val=0,
            max_val=500,
            step=1,
            extended_help='Mapping coordinates are layout pixels; active area coordinates are tablet millimeters. Zero size keeps the full default area.'
        ),
        ConfigItem(
            label='Active Area Size Y',
            key='2',
            scope='input/tablet/active_area_size',
            type_='float',
            default=0.0,
            group='Mapping',
            min_val=0,
            max_val=500,
            step=1,
            extended_help='Mapping coordinates are layout pixels; active area coordinates are tablet millimeters. Zero size keeps the full default area.'
        ),
        ConfigItem(
            label='Minimum Pen Pressure',
            key='pressure_range_min',
            scope='input/tablettool',
            type_='float',
            default=-1.0,
            group='Pen',
            min_val=-1,
            max_val=1,
            step=0.05,
            extended_help='Any negative value uses the device pressure range; otherwise set a normalized value between 0 and 1.'
        ),
        ConfigItem(
            label='Maximum Pen Pressure',
            key='pressure_range_max',
            scope='input/tablettool',
            type_='float',
            default=-1.0,
            group='Pen',
            min_val=-1,
            max_val=1,
            step=0.05,
            extended_help='Any negative value uses the device pressure range; otherwise set a normalized value between 0 and 1.'
        ),
    ],
}

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import sys, subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
