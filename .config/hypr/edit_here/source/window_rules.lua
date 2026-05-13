-- =============================================================================
-- USER CONFIGURATION: window_rules.lua
-- =============================================================================
-- Add your custom window rules here.
-- These will override or add to the defaults found in
-- ~/.config/hypr/source/window_rules.lua
-- =============================================================================

hl.config({
    layerrule = {
        -- Disable selection-tool animation during area screenshots
        "noanim, ^selection$"
    },
    
    windowrulev2 = {
        -- Force emulators to bypass tiling and hit fullscreen immediately
        "fullscreen, class:^(com.libretro.RetroArch)$",
        "fullscreen, class:^(info.cemu.Cemu)$",
        
        -- If that truncated 'i>' was 'idleinhibit focus', you just add 
        -- another string for the same class like this:
        "idleinhibit focus, class:^(com.libretro.RetroArch)$",
        "idleinhibit focus, class:^(info.cemu.Cemu)$"
    }
})
