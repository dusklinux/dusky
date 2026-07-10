require("edit_here.source.default_apps")

-- -----------------------------------------------------
-- DEFAULT APPS & USER VARIABLES
-- Must be FIRST so that globals (terminal, browser, etc.)
-- defined in this file are available to every file below.
-- Managed by: ~/.config/hypr/edit_here/source/default_apps.lua
-- -----------------------------------------------------




-- -----------------------------------------------------
-- Path to home
-- -----------------------------------------------------
HOME = os.getenv("HOME")



-- -----------------------------------------------------
-- Path to scripts
-- -----------------------------------------------------

dusky_scripts = HOME .. "/user_scripts/"



-- -----------------------------------------------------
-- Path to matugen colors
-- -----------------------------------------------------

dofile(HOME .. "/.config/matugen/generated/hyprland-colors.lua")

-- =============================================================================
-- HYPRLAND MAIN CONFIGURATION
-- User: [dusky]
-- System: UWSM Managed
-- =============================================================================
-- NOTE: All files are loaded with require() — NOT dofile().
-- Hyprland gives each require() call its own error-isolated scope, so a
-- syntax error in one file will not abort loading of the remaining files.
-- Paths are dot-separated and relative to ~/.config/hypr/:
--   require("source.monitors")  ->  ~/.config/hypr/source/monitors.lua
-- =============================================================================


-- -----------------------------------------------------
-- SOURCE FILES
-- -----------------------------------------------------

-- 1. MONITORS
-- Must be first among display config. Everything (workspaces, scaling,
-- bar positioning) depends on the physical layout being established.
require("source.monitors")

-- 2. PROGRAMS & ENVIRONMENT
-- Polkit agents, Hyprland-specific env vars.
-- Note: Most env vars are handled by ~/.config/uwsm/{env,env-hyprland}.
-- Only put vars here that UWSM should NOT see.
require("source.permissions")

-- 3. PLUGINS (commented out — enable when needed)
-- Plugins must be loaded before appearance/keybinds if those files
-- reference plugin-specific variables or dispatchers.
-- require("source.plugins")

-- 4. INPUT DEVICES
-- Keyboard layouts, mouse sensitivity, touchpad gestures.
-- Loaded early so keybinds map correctly to devices.
require("source.input")


-- 4a. Trackpad
-- touchpad gestures.
-- require("source.trackpad")


-- 5. APPEARANCE
-- General settings, decorations (rounding, blur), animations, colors.
require("source.appearance")

-- 6. WINDOW RULES & WORKSPACES
-- Defines how windows behave (floating, tiling, opacity) before they open.
require("source.window_rules")

-- 7. KEYBINDINGS
-- Loaded after everything else so all dispatchers (plugin or standard)
-- and rules are already available. Uses globals from default_apps.lua.
require("source.keybinds")

-- 8. AUTOSTART
-- Uses UWSM: ensure this file calls hl.on("hyprland.start", ...) with
-- uwsm-app where needed. Loaded LATE so monitors, inputs, and window
-- rules are fully applied before apps launch.
require("source.autostart")

-- 9. ENVIRONMENT VARIABLES
require("source.environment_variables")

-- 10. WORKSPACE RULES
require("source.workspace_rules")

-- 11. GPU env
pcall(require, "gpu")

-- -----------------------------------------------------
-- LOCAL OVERRIDES  (git-ignored)
-- -----------------------------------------------------
-- Machine-specific overrides (e.g. work vs home monitor layout) or
-- in-progress changes you don't want to commit yet.
-- Loaded LAST so it can overwrite any setting above.
-- Managed by: ~/.config/hypr/edit_here/

-- Source User Custom Config Overlay
require("edit_here.hyprland")
