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
hyprctl dispatch "hl.dsp.focus({workspace=1})" >/dev/null 2>&1 || :

# 3. Smart teardown logic
# Safely check if UWSM is installed, then ask UWSM if it is actively managing the session
if command -v uwsm >/dev/null 2>&1 && uwsm check is-active >/dev/null 2>&1; then

    # --- UWSM MANAGED TEARDOWN ---
    # UWSM handles graceful application termination natively via systemd targets.
    # We execute the action directly, allowing PID 1 to orchestrate the teardown.
    if [[ "$ACTION" == "logout" ]]; then
        exec uwsm stop
    else
        exec systemctl "$ACTION" --no-wall
    fi

else

    # --- STANDALONE HYPRLAND TEARDOWN ---
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
        exec hyprctl dispatch "hl.dsp.exit()"
    else
        exec systemctl "$ACTION" --no-wall
    fi

fi
