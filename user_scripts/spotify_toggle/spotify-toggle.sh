#!/bin/bash

# Check if spotify process is running
if pgrep -x "spotify" > /dev/null; then
    # If running, just toggle the special workspace
    hyprctl dispatch movetoworkspacesilent "special:music,class:^(spotify)$"
    hyprctl dispatch togglespecialworkspace music
else
    # If not running, launch it
    spotify-launcher &

    # Wait for the window to actually appear (timeout after 5 seconds)
    count=0
    while [ $count -lt 50 ]; do
        if hyprctl clients | grep -q "class: spotify"; then
            break
        fi
        sleep 0.1
        ((count++))
    done

    # Force move the window to special:music (quoted to fix syntax error)
    hyprctl dispatch movetoworkspacesilent "special:music,class:^(spotify)$"
    
    # Show the workspace
    hyprctl dispatch togglespecialworkspace music
fi
