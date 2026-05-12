-- =============================================================================
-- USER CONFIGURATION: input.lua
-- =============================================================================
-- Add your custom input settings here.
-- These will override or add to the defaults found in
-- ~/.config/hypr/source/input.lua
-- This file can also be edited with:
--   dusky input  (from the Rofi menu or Dusky Control Center)
-- =============================================================================

-- -------------------------------------------------------------------------------------------------
-- 1. KEYBOARD & LANGUAGE
-- -------------------------------------------------------------------------------------------------
hl.config({
    input = {
        kb_layout  = "us",
        kb_options = "",       -- e.g. "caps:escape", "grp:alt_shift_toggle"

        resolve_binds_by_sym = false,
        numlock_by_default   = true,
        repeat_rate  = 35,
        repeat_delay = 250,

        -- -----------------------------------------------------------------
        -- 2. MOUSE & POINTER ACCELERATION
        -- -----------------------------------------------------------------
        follow_mouse   = 1,
        sensitivity    = -0.4,
        accel_profile  = "flat",
        force_no_accel = true,
        left_handed    = false,
        mouse_refocus  = true,

        -- -----------------------------------------------------------------
        -- 3. SCROLLING & TRACKBALLS
        -- -----------------------------------------------------------------
        natural_scroll     = false,
        scroll_method      = "2fg",
        scroll_button      = 0,
        scroll_button_lock = false,

        -- -----------------------------------------------------------------
        -- 4. TOUCHPAD
        -- -----------------------------------------------------------------
        touchpad = {
            natural_scroll       = true,
            disable_while_typing = true,
            tap_to_click         = true,
            clickfinger_behavior = false,
            drag_lock            = false,
        },
    },
})

-- -------------------------------------------------------------------------------------------------
-- 5. CURSOR BEHAVIOR & RENDERING
-- -------------------------------------------------------------------------------------------------
hl.config({
    cursor = {
        sync_gsettings_theme = true,
        no_hardware_cursors  = 2,
        use_cpu_buffer       = 2,
        hide_on_key_press    = false,
        inactive_timeout     = 0,
        warp_on_change_workspace = 0,
        no_break_fs_vrr      = 2,
        zoom_factor          = 1.0,
    },
})

-- -------------------------------------------------------------------------------------------------
-- 6. GESTURE PHYSICS (Tuning)
-- -------------------------------------------------------------------------------------------------
hl.config({
    gestures = {
        workspace_swipe_distance     = 300,
        workspace_swipe_cancel_ratio = 0.5,
        workspace_swipe_invert       = true,
        workspace_swipe_create_new   = true,
        workspace_swipe_forever      = false,
    },
})
