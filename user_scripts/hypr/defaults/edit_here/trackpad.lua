-- ==============================================================================
-- USER CONFIGURATION: trackpad.lua
-- ==============================================================================
-- Continuous 1:1 Lua gestures for Hyprland 0.57+

-- ── 3-Finger Gestures ────────────────────────────────────────────────────────────────────────────

-- Left/Right: Native 1:1 smooth workspace switching
hl.gesture({
    fingers   = 3,
    direction = "horizontal",
    action    = "workspace",
})

-- Up: Open Dusky Tray
hl.gesture({
    fingers   = 3,
    direction = "up",
    action    = function()
        hl.exec_cmd([["$HOME/.local/bin/dusky_tray"]])
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

-- ── 4-Finger Continuous Gestures ─────────────────────────────────────────────────────────────────

-- Vertical (Up/Down): Smooth continuous Volume control
hl.gesture({
    fingers   = 4,
    direction = "vertical",
    action    = {
        start = function(e)
            local now = (e and e.time_ms) or 0
            _G._dusky_vol_acc = (-((e and e.delta and e.delta.y) or 0)) * 0.25
            _G._dusky_vol_ms  = now
            _G._dusky_vol_dir = 0
        end,
        update = function(e)
            local d = (-((e and e.delta and e.delta.y) or 0)) * 0.25
            local acc = (_G._dusky_vol_acc or 0)
            -- Clear opposing fractional remainder on direction reversal to prevent lift-off jitter
            if (d > 0 and acc < 0) or (d < 0 and acc > 0) then
                acc = acc * 0.25
            end
            acc = acc + d
            local step = math.floor(math.abs(acc))
            local now = (e and e.time_ms) or 0
            local last = _G._dusky_vol_ms or 0
            if step >= 2 and (now < last or (now - last) >= 35) then
                if step > 100 then step = 100 end
                local dir = acc > 0 and 1 or -1
                local flag = dir > 0 and "--vol-up " or "--vol-down "
                _G._dusky_vol_acc = acc - (dir > 0 and step or -step)
                _G._dusky_vol_ms  = now
                _G._dusky_vol_dir = dir
                hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh " .. flag .. step)
            else
                _G._dusky_vol_acc = acc
            end
        end,
        finish = function(e)
            local acc = (_G._dusky_vol_acc or 0)
            local last_dir = _G._dusky_vol_dir or 0
            _G._dusky_vol_acc = 0
            _G._dusky_vol_dir = 0
            if not (e and e.cancelled) then
                local step = math.floor(math.abs(acc))
                local dir = acc > 0 and 1 or -1
                -- Suppress opposite-direction finger-lift rebound while preserving same-direction and micro-swipe steps
                if step >= 1 and (last_dir == 0 or dir == last_dir) then
                    if step > 100 then step = 100 end
                    local flag = dir > 0 and "--vol-up " or "--vol-down "
                    hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh " .. flag .. step)
                end
            end
        end,
    },
})

-- Horizontal (Left/Right): Smooth continuous Brightness control
hl.gesture({
    fingers   = 4,
    direction = "horizontal",
    action    = {
        start = function(e)
            local now = (e and e.time_ms) or 0
            local seq = (_G._dusky_bright_seq or 0) + 1
            _G._dusky_bright_seq = seq
            _G._dusky_bright_id  = tostring(math.abs(math.floor(now))) .. "_" .. tostring(seq)
            _G._dusky_bright_acc = ((e and e.delta and e.delta.x) or 0) * 0.25
            _G._dusky_bright_ms  = now
            _G._dusky_bright_dir = 0
        end,
        update = function(e)
            local d = ((e and e.delta and e.delta.x) or 0) * 0.25
            local acc = (_G._dusky_bright_acc or 0)
            -- Clear opposing fractional remainder on direction reversal to prevent lift-off jitter
            if (d > 0 and acc < 0) or (d < 0 and acc > 0) then
                acc = acc * 0.25
            end
            acc = acc + d
            local step = math.floor(math.abs(acc))
            local now = (e and e.time_ms) or 0
            local last = _G._dusky_bright_ms or 0
            if step >= 2 and (now < last or (now - last) >= 25) then
                if step > 100 then step = 100 end
                local dir = acc > 0 and 1 or -1
                local flag = dir > 0 and "--bright-up " or "--bright-down "
                local gid = _G._dusky_bright_id or "0"
                _G._dusky_bright_acc = acc - (dir > 0 and step or -step)
                _G._dusky_bright_ms  = now
                _G._dusky_bright_dir = dir
                hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh " .. flag .. step .. " " .. gid)
            else
                _G._dusky_bright_acc = acc
            end
        end,
        finish = function(e)
            local acc = (_G._dusky_bright_acc or 0)
            local gid = _G._dusky_bright_id or "0"
            local last_dir = _G._dusky_bright_dir or 0
            _G._dusky_bright_acc = 0
            _G._dusky_bright_id  = nil
            _G._dusky_bright_dir = 0
            if not (e and e.cancelled) then
                local step = math.floor(math.abs(acc))
                local dir = acc > 0 and 1 or -1
                -- Suppress opposite-direction finger-lift rebound while preserving same-direction and micro-swipe steps
                if step >= 1 and (last_dir == 0 or dir == last_dir) then
                    if step > 100 then step = 100 end
                    local flag = dir > 0 and "--bright-up " or "--bright-down "
                    hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh " .. flag .. step .. " " .. gid)
                end
            end
        end,
    },
})

-- -------------------------------------------------------------------------------------------------
-- GESTURE PHYSICS  (controls feel of the workspace swipe gesture, not gesture definitions)
-- -------------------------------------------------------------------------------------------------
hl.config({
    gestures = {
        workspace_swipe_distance                 = 300,   -- Max swipe travel distance in px.
        workspace_swipe_invert                   = true,  -- Invert swipe direction.
        workspace_swipe_min_speed_to_force       = 30,    -- Min px/timepoint speed to force workspace change (0 = disable).
        workspace_swipe_cancel_ratio             = 0.5,   -- Fraction of distance needed to commit (0.0–1.0).
        workspace_swipe_create_new               = true,  -- Create a new workspace when swiping past the last one.
        workspace_swipe_direction_lock           = true,  -- Lock swipe axis after passing direction threshold.
        workspace_swipe_direction_lock_threshold = 10,    -- Distance in px before direction lock engages.
        workspace_swipe_forever                  = false, -- Allow swiping past neighbouring workspaces without stopping.
        workspace_swipe_use_r                    = false, -- Use 'r' prefix (relative) instead of 'm' prefix for workspaces.
        close_max_timeout                        = 1000,  -- Max ms a 1:1 gesture window has to close, in ms.
    },
})

-- Additional TUI controls; existing configuration layers keep their load order.
hl.config({
    gestures = {
        workspace_swipe_touch        = false,
        workspace_swipe_touch_invert = false,
        scrolling = {
            move_snap_to_grid = true,
            move_snap_cursor  = true,
        },
    },
})
