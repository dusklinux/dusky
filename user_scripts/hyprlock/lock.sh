#!/usr/bin/env bash

# Cache the current wallpaper path
# Wallpaper refresh is optional: an unavailable daemon must not prevent locking.
if wallpaper_info=$(awww query 2>/dev/null) && [[ $wallpaper_info == *'image: '* ]]; then
    wallpaper=${wallpaper_info#*image: }
    wallpaper=${wallpaper%%$'\n'*}
    if [[ -f $wallpaper ]]; then
        cp -- "$wallpaper" "$HOME/.cache/current_wallpaper"
    fi
fi

# Launch hyprlock
exec hyprlock
