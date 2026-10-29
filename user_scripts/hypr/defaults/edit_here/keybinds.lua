-- ==============================================================================
-- USER CONFIGURATION: keybinds.lua
-- ==============================================================================
-- Add your custom keybinds here.
-- These will override or add to the defaults found in ~/.config/hypr/source/
-- This file can also be managed with dusky keybinds manager from the rofi
-- menu or from dusky control center.
--
-- Syntax:
--   local mainMod = "SUPER"
--   hl.bind(mainMod .. " + Q", hl.dsp.exec_cmd(terminal))
--   hl.bind(mainMod .. " + Q", hl.dsp.exec_cmd("kitty"), { description = "Launch terminal" })
--
-- NOTE: 'terminal', 'browser', etc. are globals defined in default_apps.lua.
--
-- See: https://wiki.hypr.land/configuring/core/binds/
-- ==============================================================================

-- local mainMod = "SUPER"

hl.bind(
    "SUPER + Q",
    hl.dsp.exec_cmd("dusky-run " .. terminal),
    { description = "Launch Terminal", submap_universal = true  }
)

hl.bind(
    "SUPER + W",
    hl.dsp.exec_cmd("dusky-run " .. browser),
    { description = "Launch Browser", submap_universal = true  }
)

hl.bind(
    "SUPER + E",
    hl.dsp.exec_cmd("dusky-run " .. fileManager),
    { description = "File Manager", submap_universal = true  }
)

hl.bind(
    "SUPER + R",
    hl.dsp.exec_cmd("dusky-run " .. textEditor),
    { description = "Open Text Editor", submap_universal = true  }
)
