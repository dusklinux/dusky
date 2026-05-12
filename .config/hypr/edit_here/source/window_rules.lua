-- =============================================================================
-- USER CONFIGURATION: window_rules.lua
-- =============================================================================
-- Add your custom window rules here.
-- These will override or add to the defaults found in
-- ~/.config/hypr/source/window_rules.lua
-- =============================================================================

-- Force emulators to bypass tiling and hit fullscreen immediately
hl.window_rule({ match = { class = "^(com.libretro.RetroArch)$"      }, fullscreen = true, idle_inhibit = "always" })
hl.window_rule({ match = { class = "^(info.cemu.Cemu)$"              }, fullscreen = true, idle_inhibit = "always" })
