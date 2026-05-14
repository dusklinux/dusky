-- -------------------------------------------------------------------------------------------------
-- TRACKPAD GESTURES
-- -------------------------------------------------------------------------------------------------
-- NOTE: Gestures fire once per recognized swipe, not continuously.
--       Volume/brightness step is 5% per swipe — do multiple quick swipes for larger changes.
--       Tap gestures are not supported by Hyprland natively (as of 0.55).
--       For your 3-finger tap QuickPanel: use ALT+V (already bound in keybinds).

-- ── 3-Finger Gestures ────────────────────────────────────────────────────────────────────────────

-- Left/Right: Native 1:1 smooth workspace switching (no plugin needed)
hl.gesture({
    fingers   = 3,
    direction = "horizontal",
    action    = "workspace",
})

hl.gesture({
    fingers   = 3,
    direction = "up",
    action    = function()
        hl.exec_cmd("")
    end,
})

-- Down: Toggle media pause/play
hl.gesture({
    fingers   = 3,
    direction = "down",
    action    = function()
        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --play-pause")
    end,
})

-- ── 4-Finger Gestures ────────────────────────────────────────────────────────────────────────────

-- Left/Right: Volume control (5% per swipe, capped at 150% to prevent distortion)
hl.gesture({
    fingers   = 4,
    direction = "left",
    action    = function()
        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --vol-down 10")
    end,
})

hl.gesture({
    fingers   = 4,
    direction = "right",
    action    = function()
        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --vol-up 10")
    end,
})

hl.gesture({
    fingers   = 4,
    direction = "up",
    action    = function()
        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --bright-up 10")
    end,
})

hl.gesture({
    fingers   = 4,
    direction = "down",
    action    = function()
        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --bright-down 10")
    end,
})



-- -------------------------------------------------------------------------------------------------
-- GESTURE PHYSICS  (controls feel of the workspace swipe gesture, not gesture definitions)
-- -------------------------------------------------------------------------------------------------
hl.config({
    gestures = {
        workspace_swipe_distance           = 300,  -- Max swipe travel distance in px.
        workspace_swipe_invert             = true, -- Invert swipe direction.
        workspace_swipe_min_speed_to_force = 30,   -- Min px/timepoint speed to force workspace change (0 = disable).
        workspace_swipe_cancel_ratio       = 0.5,  -- Fraction of distance needed to commit (0.0–1.0).
        workspace_swipe_create_new         = true, -- Create a new workspace when swiping past the last one.
        workspace_swipe_direction_lock     = true, -- Lock swipe axis after passing direction threshold.
        workspace_swipe_direction_lock_threshold = 10, -- Distance in px before direction lock engages.
        workspace_swipe_forever            = false, -- Allow swiping past neighbouring workspaces without stopping.
        workspace_swipe_use_r              = false, -- Use 'r' prefix (relative) instead of 'm' prefix for workspaces.
        close_max_timeout                  = 1000, -- Max ms a 1:1 gesture window has to close, in ms.
    },
})
