-- =============================================================================
-- AUTOSTART — Base Services
-- =============================================================================
-- Optimized for: Arch Linux | Hyprland | UWSM
-- Wrap all apps in 'uwsm-app --' so systemd tracks them properly.
-- =============================================================================

local home = os.getenv("HOME")

-- --- BACKGROUND SERVICES ---

-- Wallpaper engine
hl.on("hyprland.start", function() hl.exec_cmd("uwsm-app -- awww-daemon") end)

-- hypridle is managed via its systemd service — no exec-once needed.

-- --- CLIPBOARD MANAGER ---
hl.on("hyprland.start", function()
    hl.exec_cmd("uwsm-app -- wl-paste --type text --watch cliphist store")
    hl.exec_cmd("uwsm-app -- wl-paste --type image --watch cliphist store")
    hl.exec_cmd("uwsm-app -- wl-clip-persist --clipboard regular")
end)

-- --- OPTIONAL / USER INTERFACE ---
hl.on("hyprland.start", function()
    hl.exec_cmd("uwsm-app -- " .. home .. "/user_scripts/waybar/waybar_autostart.sh")
end)

-- --- SLOW APP LAUNCH FIX — propagate environment to systemd and dbus ---
hl.on("hyprland.start", function()
    hl.exec_cmd("systemctl --user import-environment $(env | cut -d'=' -f 1)")
    hl.exec_cmd("dbus-update-activation-environment --systemd --all")
end)
