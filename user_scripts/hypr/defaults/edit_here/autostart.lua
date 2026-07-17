-- ==============================================================================
-- USER CONFIGURATION: autostart.lua
-- ==============================================================================
-- Add your custom autostart entries here.
-- These will override or add to the defaults found in ~/.config/hypr/source/
--
-- Syntax:
--   hl.on("hyprland.start", function()
--       hl.exec_cmd("waybar")
--       hl.exec_cmd("nm-applet")
--   end)
--
-- See: https://wiki.hypr.land/Configuring/Basics/Autostart/
-- ==============================================================================

-- --- XWAYLAND CONFIGURATION ---
-- to disable xwayland to save 20-30 mbs of ram, disabling will prevent xwayland apps from working
-- Uncomment the block below to apply:
hl.config({
    xwayland = {
        enabled = true
    }
})

-- -------------------------------------------------------------------------------------------------
-- AUTOSTART COMMANDS
-- -------------------------------------------------------------------------------------------------
hl.on("hyprland.start", function()


    -- XHost: Grants root access to the display (needed for GParted/Synaptic to run).
    -- make sure to install xorg-xhost beofre uncommenting the following line, sudo pacman -S xorg-xhost
    -- hl.exec_cmd("xhost +si:localuser:root")

    -- --- BACKGROUND SERVICES ---
    hl.exec_cmd("awww-daemon")           -- Wallpaper engine
    hl.exec_cmd("$HOME/user_scripts/way_layers/visualizer/visualizer_toggle.sh") -- Audio Visualizer

    -- --- OPTIONAL / USER INTERFACE ---
    hl.exec_cmd("$HOME/user_scripts/waybar/waybar_toggle.sh")
    -- hl.exec_cmd("$HOME/user_scripts/waybar/toggle_timer_waybar.sh")
    -- hl.exec_cmd("nm-applet")

    -- EG: dusky glance (uncomment only one at a time)
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --cpu")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --cpu-power")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --ram")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --ram-temp")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --temp")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery-percent")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery-watts")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery-time")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --gpu-power card1 Intel")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --gpu-usage card1 Intel")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --gpu-mem card1 Intel")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --uptime")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --workspace")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --clock")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --clock-short")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --disk")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --disk-read nvme0n1")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --disk-write nvme0n1")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --disk-temp nvme0n1")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --zram")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --stopwatch")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --timer 15m")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --hud card1 Intel")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --world-clock America/New_York NY")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --world-clock Asia/Tokyo Japan")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --world-clock Europe/London London")

end)

-- hl.on("hyprland.shutdown", function()
--    hl.exec_cmd("systemctl --user stop hyprland-session.target")
-- end)
