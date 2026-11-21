hl.on("hyprland.start", function()

    -- --- Sync variables with D-Bus and Systemd ---
    -- exec_cmd is asynchronous: use one shell so imports finish before services start.
    -- A D-Bus update failure must not prevent startup after systemd's import succeeds.
    hl.exec_cmd("systemctl --user import-environment WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE XDG_SESSION_ID XDG_CURRENT_DESKTOP XDG_SESSION_TYPE XDG_SESSION_DESKTOP XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME PATH CLIPHIST_DB_PATH && { dbus-update-activation-environment --systemd --all; systemctl --user start hyprland-session.target; }")
    -- --- SYSTEM ESSENTIALS ---

    -- Gnome Keyring: Stores passwords for apps (VSCode, Chrome, etc.). (recommanded to enable systemd service instead of auto starting with exec-once)
    -- hl.exec_cmd("/usr/bin/gnome-keyring-daemon --start --components=secrets")
    -- OR
    -- replace the exec-once line with:
    -- hl.exec_cmd("systemctl --user start gnome-keyring-daemon.service")

    -- --- Protect Compositor from OOM Killer ---
    hl.exec_cmd("sudo choom -n -250 -p $(pgrep -x Hyprland)")

    -- --- Restore the last chosen power profile (TLP) ---
    -- Re-applies the profile picked in Dusky Center / Waybar before the last
    -- shutdown. No-op without TLP, without a saved choice, or when "auto" was chosen.
    hl.exec_cmd("$HOME/user_scripts/battery/tlp/tlp_mode_toggle.sh restore")

    -- hl.exec_cmd("$HOME/user_scripts/hypr/layout_notify.sh") -- Keyboard Layout Notify

    -- --- CLIPBOARD MANAGER (Systemd Managed) ---
    -- Managed via unified systemd user service: dusky_clipboard.service
    -- bound to graphical-session.target.
    -- The service supervises the watchers and restarts on failure during the session.


end)

hl.on("hyprland.shutdown", function()
    hl.exec_cmd("systemctl --user stop hyprland-session.target")
end)
