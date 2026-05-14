-- -------------------------------------------------------------------------------------------------
-- INPUT  (keyboard · mouse · tablet · virtual keyboard)
-- -------------------------------------------------------------------------------------------------
hl.config({
    input = {
        -- --- Keyboard ---
        kb_model        = "",            -- XKB keymap model.
        kb_layout       = "us",          -- XKB keymap layout.
        kb_variant      = "",            -- XKB keymap variant.
        kb_options      = "",            -- XKB keymap options.
        kb_rules        = "",            -- XKB keymap rules.
        kb_file         = "",            -- Path to a custom .xkb file (overrides the above).
        numlock_by_default   = false,    -- Engage numlock on startup.
        resolve_binds_by_sym = false,    -- How keybinds behave with multiple layouts active.
        repeat_rate      = 35,           -- Key repeat rate in repeats/sec.
        repeat_delay     = 250,          -- Delay in ms before key repeat starts.

        -- --- Mouse & Pointer ---
        sensitivity           = 0.0,    -- libinput sensitivity, clamped -1.0 to 1.0.
        accel_profile         = "adaptive", -- "adaptive", "flat", or "custom".
        force_no_accel        = false,   -- Bypass all acceleration; raw signal only.
        rotation              = 0,       -- Device rotation in degrees clockwise.
        left_handed           = false,   -- Swap LMB and RMB.

        -- --- Scrolling ---
        scroll_points             = "",  -- Custom acceleration profile (only with accel_profile = "custom").
        scroll_method             = "2fg", -- "2fg", "edge", "on_button_down", "no_scroll".
        scroll_button             = 0,   -- Scroll button; 0 = default.
        scroll_button_lock        = false, -- Lock scroll button logically (hold to scroll mode).
        scroll_factor             = 1.0, -- Scroll speed multiplier for external mice.
        natural_scroll            = false, -- Invert scroll direction (content follows finger).
        emulate_discrete_scroll   = 1,   -- Discretise hi-res scroll events: 0=off 1=non-std 2=all.

        -- --- Focus & Window Interaction ---
        follow_mouse              = 1,   -- 0=disabled 1=full 2=loose 3=fully decoupled.
        follow_mouse_shrink       = 0,   -- Shrink inactive window hit-boxes by this many px.
        follow_mouse_threshold    = 0.0, -- Minimum cursor travel (px) to change focus.
        focus_on_close            = 0,   -- After closing: 0=next 1=under cursor 2=recent.
        mouse_refocus             = true,  -- Re-focus on mouse move when follow_mouse=1.
        float_switch_override_focus = 1, -- Focus window under cursor on tiled↔float change.
        special_fallthrough       = false, -- Float-only special WS won't block regular WS focus.
        off_window_axis_events    = 1,   -- Axis events outside focused window: 0=ignore 1=out-of-bounds 2=fake 3=warp.

        -- --- Touchpad ---
        touchpad = {
            disable_while_typing    = true,  -- Suppress touchpad while keys are held.
            natural_scroll          = true,  -- Invert scroll direction.
            scroll_factor           = 1.0,   -- Touchpad scroll speed multiplier.
            middle_button_emulation = false, -- LMB + RMB simultaneously = middle click.
            tap_button_map          = "",    -- Tap button mapping: "" | "lrm" | "lmr".
            clickfinger_behavior    = false, -- 1/2/3-finger click = LMB/RMB/MMB.
            tap_to_click            = true,  -- 1/2/3-finger tap = LMB/RMB/MMB.
            drag_lock               = 0,     -- Lift-and-continue drag: 0=off 1=timeout 2=sticky.
            tap_and_drag            = true,  -- Enable tap-and-drag mode.
            flip_x                  = false, -- Invert horizontal axis.
            flip_y                  = false, -- Invert vertical axis.
            drag_3fg                = 0,     -- Three-finger drag: 0=off 1=3 fingers 2=4 fingers.
        },

        -- --- Touch Device ---
        touchdevice = {
            transform = -1,           -- Input transform; -1 = unset.
            output    = "[[Auto]]",   -- Monitor to bind touch device to.
            enabled   = true,         -- Enable or disable touch input.
        },

        -- --- Tablet ---
        tablet = {
            transform                = -1,          -- Input transform; -1 = unset.
            output                   = "",           -- Monitor to bind tablet to (empty = all monitors).
            region_position          = { 0, 0 },     -- Mapped region position relative to top-left.
            absolute_region_position = false,        -- Treat region_position as absolute monitor coords.
            region_size              = { 0, 0 },     -- Mapped region size (0,0 = full tablet area).
            relative_input           = false,        -- Use relative instead of absolute input.
            left_handed              = false,        -- Rotate tablet 180°.
            active_area_size         = { 0, 0 },     -- Tablet active area in mm (0,0 = full).
            active_area_position     = { 0, 0 },     -- Position of active area in mm.
        },

        -- --- Virtual Keyboard ---
        virtualkeyboard = {
            share_states              = 2,     -- Unify key states with other keyboards.
            release_pressed_on_close  = false, -- Release all keys when virtual keyboard closes.
        },
    },
})

-- -------------------------------------------------------------------------------------------------
-- CURSOR  (rendering · zoom · warping · hiding)
-- -------------------------------------------------------------------------------------------------
-- NOTE: cursor.zoom_disable_aa is set to true in the zoom keybinds file; do not duplicate here.
hl.config({
    cursor = {
        invisible                    = false,  -- Hide the cursor entirely.
        sync_gsettings_theme         = true,   -- Sync XCursor theme with gsettings.
        no_hardware_cursors          = 2,      -- HW cursor mode: 0=force hw 1=force sw 2=auto.
        no_break_fs_vrr              = 2,      -- Skip frame scheduling on cursor move in FS+VRR: 0=off 1=on 2=auto.
        min_refresh_rate             = 24,     -- Minimum refresh rate (Hz) when no_break_fs_vrr is active.
        hotspot_padding              = 1,      -- Padding in logical px between cursor and screen edge.
        inactive_timeout             = 0.0,    -- Hide cursor after this many seconds idle (0 = never).
        no_warps                     = false,  -- Suppress cursor warps on focus/keybinds.
        persistent_warps             = false,  -- Cursor returns to its last position inside the window on refocus.
        warp_on_change_workspace     = 0,      -- Warp to last focused window on workspace switch: 0=off 1=on.
        warp_on_toggle_special       = 0,      -- Warp to last focused window on special WS toggle: 0=off 1=on.
        default_monitor              = "[[EMPTY]]", -- Force cursor to this monitor on startup.
        zoom_factor                  = 1.0,    -- Cursor zoom level; minimum 1.0.
        zoom_rigid                   = false,  -- Rigidly lock zoom to cursor (vs. loose follow).
        zoom_detached_camera         = true,   -- Detach viewport from cursor when zoomed; camera only moves to keep cursor in view.
        enable_hyprcursor            = true,   -- Enable hyprcursor theme support.
        hide_on_key_press            = false,  -- Hide cursor on any keypress until mouse moves.
        hide_on_touch                = true,   -- Hide cursor when last input was touch.
        hide_on_tablet               = true,   -- Hide cursor when last input was tablet.
        use_cpu_buffer               = 2,      -- Use CPU buffer for HW cursors (required on Nvidia): 0=no 1=yes 2=auto.
        warp_back_after_non_mouse_input = false, -- Warp cursor back after keyboard/tablet input.
    },
})

