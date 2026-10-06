-- ==============================================================================
-- USER CONFIGURATION: input.lua
-- ==============================================================================
-- Add your custom input settings here.
-- These will override or add to the defaults found in ~/.config/hypr/source/
-- This file can also be managed with dusky input from the rofi menu or
-- from dusky control center.

-- See: https://wiki.hypr.land/Configuring/Basics/Variables/
-- See: https://wiki.hypr.land/Configuring/Advanced-and-Cool/Devices/
-- -------------------------------------------------------------------------------------------------
-- 1. INPUT (KEYBOARD, MOUSE, TOUCHPAD, TABLET, VIRTUAL KEYBOARD)
-- -------------------------------------------------------------------------------------------------
hl.config({
    input = {
        -- --- Keyboard ---
        kb_model = "",                   -- Appropriate XKB keymap parameter.
        kb_layout = "us",                -- Appropriate XKB keymap parameter.
        kb_variant = "",                 -- Appropriate XKB keymap parameter.
        kb_options = "",                 -- Appropriate XKB keymap parameter.
        kb_rules = "",                   -- Appropriate XKB keymap parameter.
        kb_file = "",                    -- If you prefer, you can use a path to your custom .xkb file.
        numlock_by_default = false,      -- Engage numlock by default.
        resolve_binds_by_sym = false,    -- Determines how keybinds act when multiple layouts are used.
        repeat_rate = 35,                -- The repeat rate for held-down keys, in repeats per second.
        repeat_delay = 250,              -- Delay before a held-down key is repeated, in milliseconds.

        -- --- Mouse & Pointer ---
        sensitivity = 0.0,               -- Sets the mouse input sensitivity. Value is clamped to the range -1.0 to 1.0.
        accel_profile = "adaptive",      -- Sets the cursor acceleration profile. Can be one of adaptive, flat, or custom.
        force_no_accel = false,          -- Force no cursor acceleration. Bypasses most pointer settings to get a raw signal.
        rotation = 0,                    -- Sets the rotation of a device in degrees clockwise off the logical neutral position.
        left_handed = false,             -- Switches RMB and LMB.

        -- --- Scrolling ---
        scroll_points = "",              -- Sets the scroll acceleration profile, when accel_profile is set to custom.
        scroll_method = "2fg",           -- Sets the scroll method. Can be one of 2fg, edge, on_button_down, no_scroll.
        scroll_button = 0,               -- Sets the scroll button. 0 means default.
        scroll_button_lock = false,      -- Toggles the button lock logically holding it down to convert motion to scroll events.
        scroll_factor = 1.0,             -- Multiplier added to scroll movement for external mice.
        natural_scroll = false,          -- Inverts scrolling direction. Scrolling moves content directly.
        emulate_discrete_scroll = 1,     -- Emulates discrete scrolling from high resolution scrolling events (0: off, 1: non-standard, 2: all).

        -- --- Focus & Interaction Behavior ---
        follow_mouse = 1,                -- Specify if and how cursor movement should affect window focus.
        follow_mouse_shrink = 0,         -- Shrinks the inactive window hitboxes used for focus detection by pixels.
        follow_mouse_threshold = 0.0,    -- Smallest distance in logical pixels the mouse needs to travel to focus a window.
        focus_on_close = 0,              -- Controls window focus behavior when a window is closed (0: next, 1: under cursor, 2: recent).
        mouse_refocus = true,            -- If disabled, mouse focus won't switch unless crossing a window boundary when follow_mouse=1.
        float_switch_override_focus = 1, -- Focus changes to window under cursor when changing tiled-to-floating and vice versa.
        special_fallthrough = false,     -- Having only floating windows in special workspace will not block focusing in regular workspace.
        off_window_axis_events = 1,      -- Handles axis events around a focused window (0: ignores, 1: out-of-bounds, 2: fakes, 3: warps).

        -- --- Touchpad (Subcategory of Input) ---
        touchpad = {
            disable_while_typing = true,     -- Disable the touchpad while typing.
            natural_scroll = true,           -- Inverts scrolling direction. Scrolling moves content directly.
            scroll_factor = 1.0,             -- Multiplier applied to the amount of scroll movement.
            middle_button_emulation = false, -- Sending LMB and RMB simultaneously will be interpreted as a middle click.
            tap_button_map = "lrm", -- 1/2/3-finger tap = left/right/middle.
            clickfinger_behavior = false,    -- Button presses with 1, 2, or 3 fingers will be mapped to LMB, RMB, and MMB respectively.
            tap_to_click = true,             -- Tapping on the touchpad with 1, 2, or 3 fingers will send LMB, RMB, and MMB respectively.
            drag_lock = 0,                   -- Lifting the finger off while dragging will not drop item (0: disabled, 1: timeout, 2: sticky).
            tap_and_drag = true,             -- Sets the tap and drag mode for the touchpad.
            flip_x = false,                  -- Inverts the horizontal movement of the touchpad.
            flip_y = false,                  -- Inverts the vertical movement of the touchpad.
            drag_3fg = 0                     -- Enables three finger drag (0: disabled, 1: 3 fingers, 2: 4 fingers).
        },

        -- --- Touchdevice (Subcategory of Input) ---
        touchdevice = {
            transform = 0, -- Normal input transform.
            output = "[[Auto]]",             -- The monitor to bind touch devices. The default is auto-detection.
            enabled = true                   -- Whether input is enabled for touch devices.
        },

        -- --- Tablet (Subcategory of Input) ---
        tablet = {
            transform = 0, -- Normal input transform.
            output = "",                     -- The monitor to bind tablets. Leave empty to map across all monitors.
            region_position = { 0, 0 },      -- Position of the mapped region in monitor layout relative to top left.
            absolute_region_position = false,-- Whether to treat the region_position as an absolute position in monitor layout.
            region_size = { 0, 0 },          -- Size of the mapped region.
            relative_input = false,          -- Whether the input should be relative.
            left_handed = false,             -- If enabled, the tablet will be rotated 180 degrees.
            active_area_size = { 0, 0 },     -- Size of tablet’s active area in mm.
            active_area_position = { 0, 0 }  -- Position of the active area in mm.
        },

        -- --- Virtual Keyboard (Subcategory of Input) ---
        virtualkeyboard = {
            share_states = 2,                -- Unify key down states and modifier states with other keyboards.
            release_pressed_on_close = false -- Release all pressed keys by virtual keyboard on close.
        }
    },

    -- ---------------------------------------------------------------------------------------------
    -- 2. CURSOR BEHAVIOR & RENDERING
    -- ---------------------------------------------------------------------------------------------
    cursor = {
        invisible = false,                   -- Don’t render cursors.
        sync_gsettings_theme = true,         -- Sync xcursor theme with gsettings.
        no_hardware_cursors = 2,             -- Disables hardware cursors. 0: use hw, 1: don't use hw, 2: auto.
        no_break_fs_vrr = 2,                 -- Disables scheduling new frames on cursor movement for fullscreen apps with VRR enabled.
        min_refresh_rate = 24,               -- Minimum refresh rate for cursor movement when no_break_fs_vrr is active.
        hotspot_padding = 1,                 -- The padding, in logical px, between screen edges and the cursor.
        inactive_timeout = 0.0,              -- In seconds, after how many seconds of cursor’s inactivity to hide it.
        no_warps = false,                    -- If true, will not warp the cursor in many cases (focusing, keybinds, etc).
        persistent_warps = false,            -- Cursor returns to its last position relative to that window, rather than to the centre.
        warp_on_change_workspace = 0,        -- Move the cursor to the last focused window after changing the workspace.
        warp_on_toggle_special = 0,          -- Move the cursor to the last focused window when toggling a special workspace.
        default_monitor = "[[EMPTY]]",       -- The name of a default monitor for the cursor to be set to on startup.
        zoom_factor = 1.0,                   -- The factor to zoom by around the cursor. Minimum 1.0.
        zoom_rigid = false,                  -- Whether the zoom should follow the cursor rigidly or loosely.
        zoom_detached_camera = true,         -- Detach the camera from the mouse when zoomed in, only ever moving to keep mouse in view.
        enable_hyprcursor = true,            -- Whether to enable hyprcursor support.
        hide_on_key_press = false,           -- Hides the cursor when you press any key until the mouse is moved.
        hide_on_touch = true,                -- Hides the cursor when the last input was a touch input until a mouse input is done.
        hide_on_tablet = true,               -- Hides the cursor when the last input was a tablet input until a mouse input is done.
        use_cpu_buffer = 2,                  -- Makes HW cursors use a CPU buffer. Required on Nvidia to have HW cursors.
        warp_back_after_non_mouse_input = false, -- Warp the cursor back to where it was after using a non-mouse input.
        zoom_disable_aa = false              -- Disable antialiasing when zooming, which means things will be pixelated.
    },

    -- ---------------------------------------------------------------------------------------------
    -- 3. GESTURE PHYSICS (Tuning)
    -- ---------------------------------------------------------------------------------------------
    gestures = {
        workspace_swipe_distance = 300,              -- In px, the distance of the touchpad gesture.
        workspace_swipe_touch = false,               -- Enable workspace swiping from the edge of a touchscreen.
        workspace_swipe_invert = true,               -- Invert the direction (touchpad only).
        workspace_swipe_touch_invert = false,        -- Invert the direction (touchscreen only).
        workspace_swipe_min_speed_to_force = 30,     -- Minimum speed in px per timepoint to force the change ignoring cancel_ratio.
        workspace_swipe_cancel_ratio = 0.5,          -- How much the swipe has to proceed in order to commence it.
        workspace_swipe_create_new = true,           -- Whether a swipe right on the last workspace should create a new one.
        workspace_swipe_direction_lock = true,       -- If enabled, switching direction will be locked when you swipe past the threshold.
        workspace_swipe_direction_lock_threshold = 10, -- In px, the distance to swipe before direction lock activates (touchpad only).
        workspace_swipe_forever = false,             -- If enabled, swiping will not clamp at the neighboring workspaces but continue.
        workspace_swipe_use_r = false,               -- If enabled, swiping will use the r prefix instead of the m prefix for finding workspaces.
        close_max_timeout = 1000                     -- The timeout for a window to close when using a 1:1 gesture, in ms.
    }
})

-- Additional TUI controls; existing configuration layers keep their load order.
hl.config({
    input = {
        tablet = {
            enabled = true,
        },
        tablettool = {
            pressure_range_min = -1.0,
            pressure_range_max = -1.0,
        },
    },
    cursor = {
        warp_on_monitor_change = -1,
    },
})
