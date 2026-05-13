-- =============================================================================
-- PLUGINS CONFIGURATION
-- =============================================================================
-- Hyprland plugins: reloads your plugins on start
-- Manage plugins with: hyprpm add / enable / disable / update
-- =============================================================================

-- Reload all installed/enabled hyprpm plugins on startup
hl.on("hyprland.start", function()
    hl.exec_cmd("hyprpm reload")
end)

-- hyprexpo plugin configuration
-- (only takes effect if hyprexpo is installed via hyprpm)
if hl.plugin and hl.plugin.hyprexpo then
    hl.plugin.hyprexpo.configure({
        columns          = 3,
        gap_size         = 5,
        bg_col           = "rgb(111111)",
        workspace_method = "first 1",  -- [center/first] [workspace]
        gesture_distance = 300,        -- how far is the "max" for the gesture
    })
end
