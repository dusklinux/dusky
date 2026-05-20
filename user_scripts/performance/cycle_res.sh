#!/bin/bash

HYPRCTL=$(command -v hyprctl || echo "/usr/bin/hyprctl")
MODETEST=$(command -v modetest || echo "/usr/bin/modetest")

RESOLUTIONS=(
    "1600x1200"
    "1440x1080"
    "1400x1050"
    "1280x960"
    "1024x768"
    "1920x1080"
)

info=$($HYPRCTL monitors | awk '
/^Monitor / { mon = $2 }
/^\t[0-9]+x[0-9]+@/ {
    split($1, a, "@")
    res = a[1]
    split(a[2], b, ".")
    ref = int(b[1])
}
/focused: yes/ { print mon, res, ref; exit }
')

MONITOR=$(echo "$info" | awk '{print $1}')
CURRENT_RES=$(echo "$info" | awk '{print $2}')
REFRESH=$(echo "$info" | awk '{print $3}')

[ -z "$REFRESH" ] && REFRESH=60

next_res=""
found=false
for i in "${!RESOLUTIONS[@]}"; do
    if [ "${RESOLUTIONS[$i]}" = "$CURRENT_RES" ]; then
        next_idx=$(( (i + 1) % ${#RESOLUTIONS[@]} ))
        next_res="${RESOLUTIONS[$next_idx]}"
        found=true
        break
    fi
done

[ "$found" = false ] && next_res="${RESOLUTIONS[0]}"

if [ -z "$MONITOR" ] || [ -z "$next_res" ]; then
    notify-send -u critical "Resolution: Could not detect monitor"
    exit 1
fi

if ! $HYPRCTL eval "hl.monitor({output='$MONITOR', mode='${next_res}@${REFRESH}', position='auto', scale=1})" 2>&1; then
    notify-send -u critical "Resolution: Failed to set ${next_res}"
    exit 1
fi

CONN_ID=$($MODETEST -M i915 -c 2>/dev/null | awk "/connected.*$MONITOR/"'{print $1}')
[ -n "$CONN_ID" ] && sudo -n $MODETEST -M i915 -w "$CONN_ID:scaling_mode:1" >/dev/null 2>&1 || true

notify-send -u low -t 1500 "Resolution: ${next_res}"
