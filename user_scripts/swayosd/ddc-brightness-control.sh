#!/usr/bin/env bash

STEP=5
STATE_FILE="/tmp/ddc-brightness"
COOLDOWN_FILE="/tmp/ddc-last-run"

# --- initialize state file if missing ---
if [[ ! -f "$STATE_FILE" ]]; then
    read -r cur_b _ <<< "$(ddcutil getvcp 10 2>/dev/null \
        | awk -F'[=,]' '/current value/{gsub(/ /,"",$2); print $2}')"
    echo "${cur_b:-50}" > "$STATE_FILE"
fi

read -r current < "$STATE_FILE"
MAX=100

case "$1" in
    status)
        pct=$current
        echo "{\"percentage\":$pct,\"text\":\"$pct%\",\"tooltip\":\"Brightness: $pct%\"}"
        exit 0
        ;;

    up|down)
        NOW=$(date +%s%3N)
        LAST=$(cat "$COOLDOWN_FILE" 2>/dev/null || echo 0)
        (( NOW - LAST < 150 )) && exit 0
        echo "$NOW" > "$COOLDOWN_FILE"

        if [[ "$1" == "up" ]]; then
            current=$(( current + STEP ))
        else
            current=$(( current - STEP ))
        fi

        [[ $current -lt 0   ]] && current=0
        [[ $current -gt MAX ]] && current=$MAX

        echo "$current" > "$STATE_FILE"

        ddcutil setvcp 10 "$current" --noverify &

        percent=$(awk "BEGIN {printf \"%.4f\", $current/$MAX}")
        [[ $current -lt 50 ]] && icon="brightness-low" || icon="brightness-high"

        swayosd-client --custom-progress "$percent" \
                       --custom-progress-text "${current}%" \
                       --custom-icon "$icon"

        pkill -RTMIN+8 waybar 2>/dev/null
        ;;

    reset)
        rm -f "$STATE_FILE"
        ;;
esac
