hl.on("hyprland.start", function()

    -- --- Sync variables with D-Bus and Systemd ---
    -- Must be first, ensures CLIPHIST_DB_PATH from environment_variables.lua reaches systemd/dbus for live switches
    hl.exec_cmd("systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE XDG_SESSION_DESKTOP CLIPHIST_DB_PATH")
    hl.exec_cmd("dbus-update-activation-environment --systemd --all")
    -- --- SYSTEM ESSENTIALS ---

    -- Gnome Keyring: Stores passwords for apps (VSCode, Chrome, etc.). (recommanded to enable systemd service instead of auto starting with exec-once)
    -- hl.exec_cmd("/usr/bin/gnome-keyring-daemon --start --components=secrets")
    -- OR
    -- replace the exec-once line with:
    -- hl.exec_cmd("systemctl --user start gnome-keyring-daemon.service")

    -- --- Start graphical session target ---
    hl.exec_cmd("systemctl --user start hyprland-session.target")

    -- --- Protect Compositor from OOM Killer ---
    hl.exec_cmd("sudo choom -n -250 -p $(pgrep -x Hyprland)")

    -- hl.exec_cmd("$HOME/user_scripts/hypr/layout_notify.sh") -- Keyboard Layout Notify

    -- --- CLIPBOARD MANAGER ---
    -- hl.exec_cmd("wl-paste --type text --watch cliphist store")
    -- hl.exec_cmd("wl-paste --type image --watch cliphist store")

    -- Custom Clipboard cliphist ---
    hl.exec_cmd("sh -c '. $HOME/.config/dusky/settings/cliphist_db_env && exec wl-paste --type text --watch cliphist store'")
    hl.exec_cmd("sh -c '. $HOME/.config/dusky/settings/cliphist_db_env && exec wl-paste --type image --watch cliphist store'")

    -- persist clipboard ---
    -- hl.exec_cmd("wl-clip-persist --clipboard regular")


end)

hl.on("hyprland.shutdown", function()
    hl.exec_cmd("systemctl --user stop hyprland-session.target")
end)
