-- =============================================================================
-- USER CONFIGURATION: autostart.lua
-- =============================================================================
-- Add your custom autostart entries here.
-- These will override or add to the defaults found in
-- ~/.config/hypr/source/autostart.lua
-- =============================================================================

local home = os.getenv("HOME")

-- Set pipewire buffer size to 128 (reduces audio latency)
hl.on("hyprland.start", function()
    hl.exec_cmd("pw-metadata -n settings 0 clock.force-quantum 128")
end)

-- Autostart solaar (Logitech peripheral manager) — hidden in tray
hl.on("hyprland.start", function()
    hl.exec_cmd("solaar --window=hide")
end)

-- Create a headless virtual output (used for Sunshine/Moonlight streaming)
-- It will appear as HEADLESS-2 based on your monitor config
hl.on("hyprland.start", function()
    hl.exec_cmd("hyprctl output create headless")
end)

-- Start Sunshine as a child process so it inherits the Hyprland socket signature
hl.on("hyprland.start", function()
    hl.exec_cmd("bash -c 'sleep 2 && sunshine'")
end)
