-- =============================================================================
-- USER CONFIGURATION OVERLAY
-- =============================================================================
-- This file sources all your custom configuration files.
-- Edit the specific files in 'source/' to apply your changes.
-- =============================================================================

local src = os.getenv("HOME") .. "/.config/hypr/edit_here/source/"

dofile(src .. "monitors.lua")
dofile(src .. "keybinds.lua")
dofile(src .. "appearance.lua")
dofile(src .. "autostart.lua")
dofile(src .. "plugins.lua")
dofile(src .. "window_rules.lua")
dofile(src .. "workspace_rules.lua")
dofile(src .. "environment_variables.lua")
dofile(src .. "input.lua")
