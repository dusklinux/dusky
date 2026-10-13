#!/usr/bin/env bash
# Hide this session's Waybar on special:magic or while the active window is
# maximized/fullscreen. Visibility is global, based on the focused monitor.
# Run inside Hyprland; an event-stream disconnect exits with failure so a
# supervisor can restart the manager. Waybar itself is left alone on exit.

set -euo pipefail

readonly SPECIAL_WORKSPACE_NAME='special:magic'
WAYBAR_BIN_PATH=''
READER_PID=''
WAYBAR_START_PID=''
WAYBAR_STOPPING=false
RETRY_STATE=false
RETRY_DELAY=1

die() {
    printf 'waybar visibility: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [[ -n $READER_PID ]]; then
        kill "$READER_PID" 2>/dev/null || true
        wait "$READER_PID" 2>/dev/null || true
    fi
    # Keep the lock file: unlinking it lets contenders lock different inodes.
}

is_waybar_running() {
    local status
    if pgrep --quiet -u "$EUID" -x waybar \
        --env "HYPRLAND_INSTANCE_SIGNATURE=$HYPRLAND_INSTANCE_SIGNATURE"; then
        return 0
    else
        status=$?
        (( status == 1 )) || die "pgrep failed (status $status)"
        return 1
    fi
}

hide_waybar() {
    local status
    # Cover the brief interval before the background child execs Waybar.
    if [[ -n $WAYBAR_START_PID ]]; then
        kill "$WAYBAR_START_PID" 2>/dev/null || true
        WAYBAR_START_PID=''
    fi
    if pkill -u "$EUID" -x waybar \
        --env "HYPRLAND_INSTANCE_SIGNATURE=$HYPRLAND_INSTANCE_SIGNATURE"; then
        :
    else
        status=$?
        (( status == 1 )) || die "pkill failed (status $status)"
    fi
    WAYBAR_STOPPING=true
}

show_waybar() {
    if is_waybar_running; then
        WAYBAR_START_PID=''
        # SIGTERM is asynchronous. Retry showing after the old bar has exited.
        $WAYBAR_STOPPING && RETRY_STATE=true
        RETRY_DELAY=0.1
        return 0
    fi
    if [[ -n $WAYBAR_START_PID ]] && kill -0 "$WAYBAR_START_PID" 2>/dev/null; then
        RETRY_STATE=true
        RETRY_DELAY=0.1
        return 0
    fi
    [[ -z $WAYBAR_START_PID ]] || die 'Waybar exited during startup; check its stderr output'
    WAYBAR_STOPPING=false
    # Neither the lock nor the event pipe may be inherited by the detached bar.
    "$WAYBAR_BIN_PATH" 8<&- 9>&- </dev/null >/dev/null &
    WAYBAR_START_PID=$!
    disown "$WAYBAR_START_PID"
    RETRY_STATE=true
    RETRY_DELAY=0.1
}

update_waybar_visibility() {
    local state visibility
    RETRY_STATE=false
    RETRY_DELAY=1

    # One IPC request and one jq process, including empty special workspaces.
    # Query errors preserve the current bar state and retry without another event.
    if ! state=$(hyprctl -j --batch 'monitors; activewindow'); then
        RETRY_STATE=true
        return 0
    fi
    if ! visibility=$(jq -ers --arg special "$SPECIAL_WORKSPACE_NAME" '
        if length != 2 or (.[0] | type) != "array" or (.[1] | type) != "object"
        then error("invalid Hyprland state") else . end
        | .[1] as $window
        | if $window != {} and
             (($window.fullscreen | type) != "number" or $window.fullscreen < 0
              or $window.fullscreen != ($window.fullscreen | floor))
          then error("invalid fullscreen state") else . end
        | [.[0][] | select(.focused == true)]
        | if length != 1 then error("no unique focused monitor") else .[0] end
        | if (.specialWorkspace.name | type) != "string"
          then error("invalid special workspace state") else . end
        | if (.specialWorkspace.name == $special)
             or (($window.fullscreen // 0) != 0)
          then "hide" else "show" end
    ' <<< "$state"); then
        RETRY_STATE=true
        return 0
    fi

    case $visibility in
        hide) hide_waybar ;;
        show) show_waybar ;;
    esac
}

main() {
    local cmd socket event_line fragment='' status
    local -a read_options
    for cmd in hyprctl jq socat cat pgrep pkill flock waybar; do
        command -v "$cmd" >/dev/null || die "missing command: $cmd"
    done
    WAYBAR_BIN_PATH=$(command -v waybar)
    [[ -n ${XDG_RUNTIME_DIR:-} && -d $XDG_RUNTIME_DIR ]] || die 'XDG_RUNTIME_DIR is unavailable'
    [[ -n ${HYPRLAND_INSTANCE_SIGNATURE:-} ]] || die 'HYPRLAND_INSTANCE_SIGNATURE is unset'
    socket="$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock"
    [[ -S $socket ]] || die "event socket is unavailable: $socket"

    # Session-specific lock, opened without truncation. Only this shell holds it.
    exec 9>>"$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/waybar_visibility_manager.lock"
    flock -n 9 || die 'another visibility manager is running, or locking failed'
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP

    # Connect before acknowledging readiness, then replace the reader with cat.
    # nofork keeps one reader process; -u leaves its stdout attached to this pipe.
    exec 8< <(exec socat -u "UNIX-CONNECT:$socket" \
        'SHELL:echo connected; exec cat,nofork,shell=/bin/sh' 9>&-)
    READER_PID=$!
    IFS= read -r -u 8 event_line && [[ $event_line == connected ]] \
        || die 'could not connect to the Hyprland event socket'
    update_waybar_visibility

    while true; do
        read_options=(-u 8)
        # No polling in steady state; retry only failed queries or pending starts.
        if $RETRY_STATE; then
            read_options+=(-t "$RETRY_DELAY")
        fi
        if IFS= read -r "${read_options[@]}" event_line; then
            event_line="$fragment$event_line"
            fragment=''
        else
            status=$?
            if (( status > 128 )); then
                # Timed reads may consume partial lines. Keep them for the next read.
                fragment+=$event_line
                update_waybar_visibility
                continue
            fi
            wait "$READER_PID" || true
            READER_PID=''
            die 'Hyprland event stream disconnected'
        fi

        case ${event_line%%>>*} in
            activespecial|workspace|focusedmon|activewindow|fullscreen|openwindow|closewindow|movewindow|moveworkspace|renameworkspace|monitoradded|monitorremoved|configreloaded)
                update_waybar_visibility
                ;;
        esac
    done
}

main "$@"
