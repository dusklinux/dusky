#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/countdown.sh"

# Returns the active workspace ID, or returns 1 with a notification on failure
get_workspace_id() {
    local ws_id
    ws_id=$(hyprctl -j activeworkspace 2>/dev/null | jq -r '.id // empty')
    if [[ -z "$ws_id" ]] || [[ "$ws_id" == "null" ]]; then
        notify-send "Error" "Could not get workspace ID" -t 3000
        return 1
    fi
    echo "$ws_id"
}

close_workspace_windows() {
    local ws_id
    ws_id=$(get_workspace_id) || return 1

    local addresses
    addresses=$(hyprctl -j clients 2>/dev/null | jq -r ".[] | select(.workspace.id == $ws_id) | .address")

    if [[ -z "$addresses" ]]; then
        notify-send "Workspace Empty" "No windows to close" -t 2000
        return 0
    fi

    local count=0
    for address in $addresses; do
        hyprctl dispatch "hl.dsp.window.close({ window = \"address:$address\" })" 2>/dev/null
        ((count++))
        sleep 0.05
    done

    notify-send "Workspace Closed" "Closed $count windows" -t 3000
}

main() {
    countdown_init "workspace-close"

    # 5-second cooldown to prevent accidental double-triggers
    if countdown_is_disabled "workspace-close"; then
        local remaining
        remaining=$(countdown_status "workspace-close" "%S remaining")
        notify-send "Cooldown Active" "Wait ${remaining}s before closing workspace again" -t 2000
        return 0
    fi

    local workspace_name ws_id window_count
    workspace_name=$(hyprctl -j activeworkspace 2>/dev/null | jq -r '.name // "Main"')
    ws_id=$(get_workspace_id) || return 1
    window_count=$(hyprctl -j clients 2>/dev/null | jq "[.[] | select(.workspace.id == $ws_id)] | length")

    if [[ "$window_count" -eq 0 ]]; then
        notify-send "Workspace Empty" "No windows to close" -t 2000
        return 0
    fi

    local confirm
    confirm=$(echo -e "Cancel\nClose $window_count windows" | rofi -dmenu \
        -p "Close all in workspace $workspace_name?" \
        -mesg "This will close all windows in this workspace")

    if [[ "$confirm" == "Close $window_count windows" ]]; then
        countdown_set "workspace-close" 5 s
        close_workspace_windows
    fi
}

main "$@"
