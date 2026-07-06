-- ==============================================================================
-- USER CONFIGURATION: environment_variables.lua
-- ==============================================================================
-- Add your custom environment variables here.
-- These will override or add to the defaults found in:
--   ~/.config/hypr/source/environment_variables.lua
--
-- NOTE: It is strongly recommended to place environment variables in the
-- UWSM files at ~/.config/uwsm/{env,env-hyprland} instead, as those are
-- sourced before Hyprland starts and apply to the full session.
--
-- Syntax:
--   hl.env("XCURSOR_SIZE",    "24")
--   hl.env("HYPRCURSOR_SIZE", "24")
--
-- See: https://wiki.hypr.land/Configuring/Advanced-and-Cool/Environment-variables/
-- ==============================================================================

-- Dynamically export current clipboard path from state file on startup
-- (Ensures terminal/GUI apps launched by Hyprland see it on non-UWSM systems)
local env_file = os.getenv("HOME") .. "/.config/dusky/settings/cliphist_db_env"
local f = io.open(env_file, "r")
if f then
    for line in f:lines() do
        local path = line:match('export CLIPHIST_DB_PATH="([^"]+)"')
        if path then
            hl.env("CLIPHIST_DB_PATH", path)
        end
    end
    f:close()
end
