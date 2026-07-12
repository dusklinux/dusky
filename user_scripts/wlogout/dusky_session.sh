#!/usr/bin/env bash

# Strict mode for robust error handling
set -euo pipefail

# Define the action, defaulting to poweroff if none is provided
ACTION="${1:-poweroff}"

# Validate input to prevent passing garbage to systemctl
case "$ACTION" in
    poweroff|reboot|soft-reboot|logout) ;;
    *)
        echo "Error: Invalid action '$ACTION'."
        echo "Usage: sys-session [poweroff|reboot|soft-reboot|logout]"
        exit 1
        ;;
esac

# 1. State management clean-up
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy"
if [[ -d "$STATE_DIR" ]]; then
    shopt -s nullglob
    # Added '--' to prevent files starting with '-' from acting as arguments
    rm -f -- "$STATE_DIR"/re*-required || :
    shopt -u nullglob
fi

# 2. Reset workspace (visually cleaner for next boot, non-fatal)
hyprctl dispatch workspace 1 >/dev/null 2>&1 || :

# --- HYPRLAND TEARDOWN ---
# Track this shell's process ancestry so we avoid aggressively closing 
# the client running this script (if invoked via terminal).
declare -A skip_pids=()
curr_pid=$$

while [[ -r "/proc/$curr_pid/status" ]]; do
    skip_pids["$curr_pid"]=1
    ppid=""

    while IFS=$': \t' read -r key value _; do
        if [[ "$key" == "PPid" ]]; then
            ppid="$value"
            break
        fi
    done < "/proc/$curr_pid/status"

    [[ "$ppid" =~ ^[0-9]+$ ]] && (( ppid > 1 )) || break
    curr_pid="$ppid"
done

batch_cmds=""

# Safely capture JSON, avoiding process substitution error masking
if clients_json=$(hyprctl clients -j 2>/dev/null); then
    if client_rows=$(jq -r '.[] | "\(.pid)\t\(.address)"' <<<"$clients_json" 2>/dev/null); then
        if [[ -n "$client_rows" ]]; then
            while IFS=$'\t' read -r c_pid addr; do
                [[ -n "${skip_pids["$c_pid"]:-}" ]] && continue
                batch_cmds+="dispatch closewindow address:${addr}; "
            done <<<"$client_rows"
        fi
    fi
fi

# Best-effort window closure; script must proceed if IPC fails
if [[ -n "$batch_cmds" ]]; then
    hyprctl --batch "$batch_cmds" >/dev/null 2>&1 || :
    sleep 1
fi

# Execute final action
if [[ "$ACTION" == "logout" ]]; then
    exec hyprctl dispatch exit
else
    exec systemctl "$ACTION" --no-wall
fi
