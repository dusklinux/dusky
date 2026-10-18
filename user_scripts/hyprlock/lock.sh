#!/usr/bin/env bash

# Themes read Matugen's current wallpaper directly, including idle/sleep locks.
# Locking must not wait for the wallpaper daemon or copy image data.
# Serialize requests per Wayland display, before Hyprlock opens a PAM prompt.
lock_name=${WAYLAND_DISPLAY//\//_}
exec flock --nonblocking --conflict-exit-code 0 --no-fork \
    "$XDG_RUNTIME_DIR/dusky-hyprlock-$lock_name.lock" hyprlock "$@"
