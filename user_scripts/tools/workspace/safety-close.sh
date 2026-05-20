#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/countdown.sh"


get_active_window_info() {
    local wininfo
    wininfo=$(hyprctl -j activewindow 2>/dev/null)

    if [[ -z "$wininfo" ]] || [[ "$wininfo" == "null" ]]; then
        return 1
    fi

    local address pid title class
    address=$(echo "$wininfo" | jq -r '.address // empty')
    pid=$(echo "$wininfo" | jq -r '.pid // empty')
    title=$(echo "$wininfo" | jq -r '.title // empty' | cut -c1-50)
    class=$(echo "$wininfo" | jq -r '.class // empty')

    echo "$address|$pid|$title|$class"
}

get_workspace_id() {
    local ws_id
    ws_id=$(hyprctl -j activeworkspace 2>/dev/null | jq -r '.id // empty')
    if [[ -z "$ws_id" ]] || [[ "$ws_id" == "null" ]]; then
        notify-send "Error" "Could not get workspace ID" -t 3000
        return 1
    fi
    echo "$ws_id"
}

close_address() {
    local address="$1"
    hyprctl dispatch "hl.dsp.window.close({ window = \"address:$address\" })" 2>/dev/null
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

show_close_menu() {
    local win_info="$1"

    # Parse the pipe-delimited window info
    local address pid title class
    address="${win_info%%|*}"
    local rest="${win_info#*|}"
    pid="${rest%%|*}"
    rest="${rest#*|}"
    title="${rest%%|*}"
    class="${rest##*|}"

    # Guard: no PID or process already dead — just close
    if [[ -z "$pid" ]] || [[ "$pid" == "null" ]] || ! kill -0 "$pid" 2>/dev/null; then
        close_address "$address"
        return
    fi

    # Safety close is disabled (countdown active) — bypass confirmation
    if countdown_is_disabled "safety-close"; then
        close_address "$address"
        notify-send "Safety Close Bypassed" "Closed window (safety timer active)" -t 2000
        return
    fi

    local workspace_name
    workspace_name=$(hyprctl -j activeworkspace 2>/dev/null | jq -r '.name // "Main"')

    # Build menu — show remaining time if a countdown file exists but isn't active
    # (i.e., the "Disable Safety Close" option shows how long is left if re-triggered)
    local disable_label="Disable Safety Close (5 min)"
    if [[ -f "$(countdown_get_file "safety-close")" ]]; then
        local status
        status=$(countdown_display "safety-close" "%m:%s")
        [[ -n "$status" ]] && disable_label="Disable Safety Close (5 min) — ($status remaining)"
    fi

    local choice
    choice=$(printf "No\n%s\nClose All In Workspace\nYes" "$disable_label" | rofi -dmenu \
        -p "Close $class (PID: $pid)?" \
        -mesg "Title: $title | Workspace: $workspace_name")

    case "$choice" in
        "No" | "")
            return 0
            ;;
        "Disable Safety Close"*)
            countdown_disable "safety-close" 5
            notify-send "Safety Close Disabled" "Close confirmation disabled for 5 minutes" -t 3000
            ;;
        "Close All In Workspace")
            local ws_name confirm
            confirm=$(echo -e "Cancel\nClose All" | rofi -dmenu \
                -p "Close all in workspace $workspace_name?" \
                -mesg "This will close all windows in this workspace")
            if [[ "$confirm" == "Close All" ]]; then
                close_workspace_windows
            fi
            ;;
        "Yes")
            countdown_set "safety-close-recent" 5 s
            close_address "$address"
            ;;
    esac
}

main() {
    countdown_init "safety-close"

    local win_info
    win_info=$(get_active_window_info) || {
        # No active window — nothing to do
        return 0
    }

    local address="${win_info%%|*}"
    if [[ -z "$address" ]]; then
        return 0
    fi

    show_close_menu "$win_info"
}

main "$@"
