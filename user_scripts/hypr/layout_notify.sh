#!/usr/bin/env bash

# Wait for Hyprland to set its Instance Signature
while [ -z "$HYPRLAND_INSTANCE_SIGNATURE" ]; do
    sleep 1
done

handle() {
    local event="$1"
    if [[ "$event" == "activelayout>>"* ]]; then
        # The event format is: activelayout>>keyboard_device_name,layout_name
        local payload="${event#activelayout>>}"
        # Split by comma to get the layout name (part after the comma)
        local layout_name="${payload#*,}"

        # Get a short version of the layout if desired, here we just use the provided name
        # Using swayosd for the OSD notification
        swayosd-client --custom-message "$layout_name" --custom-icon "input-keyboard-symbolic"
        
        # Backup regular notification just in case swayosd isn't enough for some
        # notify-send -a "Keyboard Layout" -i input-keyboard-symbolic -t 1500 -h string:x-canonical-private-synchronous:kb_layout "Layout Switched" "$layout_name"
    fi
}

# Listen to the Hyprland IPC socket using socat
socat -U - UNIX-CONNECT:"${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock" | while read -r line; do
    handle "$line"
done
