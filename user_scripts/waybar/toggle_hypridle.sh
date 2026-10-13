#!/usr/bin/env bash
# Toggle the packaged hypridle user service and refresh Waybar.
set -uo pipefail

readonly SERVICE=hypridle.service
readonly WAYBAR_SIGNAL=9 # Must match "signal" in the Waybar custom module.
readonly APP_NAME=dusky-hypridle
readonly NOTIFY_TIMEOUT=2200

is_running() {
    pgrep -u "$EUID" -x hypridle >/dev/null
}

send_notification() {
    command -v notify-send >/dev/null || return 0
    notify-send --urgency="$1" --expire-time="$NOTIFY_TIMEOUT" \
        --app-name="$APP_NAME" --icon="$4" \
        --hint="string:x-canonical-private-synchronous:$APP_NAME" \
        "$2" "$3" || :
}

update_waybar() {
    pkill -u "$EUID" -x --signal "RTMIN+$WAYBAR_SIGNAL" waybar 2>/dev/null || :
}

fail() {
    printf '%s: %s\n' "${0##*/}" "$1" >&2
    send_notification critical 'hypridle error' "$1" dialog-error
    return 1
}

main() {
    # A Wayland session supplies this directory; do not put locks in $HOME.
    if [[ -z ${XDG_RUNTIME_DIR:-} || ! -d $XDG_RUNTIME_DIR ]]; then
        fail 'XDG_RUNTIME_DIR is unavailable; run this from your Wayland session.'
        return 1
    fi

    local lock_fd
    if ! exec {lock_fd}>"$XDG_RUNTIME_DIR/dusky-hypridle.lock"; then
        fail 'Could not open the toggle lock.'
        return 1
    fi
    # Serialize clicks so each toggle observes the previous completed change.
    if ! flock "$lock_fd"; then
        exec {lock_fd}>&-
        fail 'Could not acquire the toggle lock.'
        return 1
    fi
    # Refresh on errors too: a failed operation may still have changed state.
    trap update_waybar EXIT

    local state action error='' process_status=0
    if ! state=$(systemctl --user show --property=ActiveState --value "$SERVICE"); then
        error="Could not query $SERVICE; see systemctl --user status $SERVICE."
    else
        is_running || process_status=$?
        if (( process_status > 1 )); then
            error='Could not inspect hypridle processes.'
        else
            case $state in
                # An automatic restart can be pending with no daemon PID yet.
                active|activating|deactivating|reloading|refreshing) action=stop ;;
                inactive|failed)
                    if (( process_status == 0 )); then
                        error='hypridle is running outside its service; stop that instance first.'
                    else
                        action=start
                    fi
                    ;;
                *) error="Cannot toggle $SERVICE in state '$state'; check its status." ;;
            esac
        fi
    fi

    if [[ -z $error ]]; then
        # systemd waits for shutdown and handles timeout/escalation itself.
        # An explicit stop also suppresses Restart=on-failure.
        if ! systemctl --user "$action" "$SERVICE"; then
            error="Could not $action $SERVICE; see systemctl --user status $SERVICE."
        elif [[ $action == start ]] && ! sleep 0.3; then
            # Type=simple completes its start job before daemon initialization.
            error='Startup verification was interrupted.'
        elif ! state=$(systemctl --user show --property=ActiveState --value "$SERVICE"); then
            error="Could not verify $SERVICE; see systemctl --user status $SERVICE."
        else
            process_status=0
            is_running || process_status=$?
            if (( process_status > 1 )); then
                error='Could not inspect hypridle processes.'
            elif [[ $action == stop ]]; then
                if (( process_status == 0 )) || [[ $state != inactive && $state != failed ]]; then
                    error='hypridle did not stop; check its service and any manually started instances.'
                fi
            elif (( process_status != 0 )) || [[ $state != active && $state != reloading && $state != refreshing ]]; then
                error="hypridle did not stay running; see journalctl --user -u $SERVICE."
            fi
        fi
    fi

    # Notification delivery must not delay the panel refresh or the next toggle.
    update_waybar
    trap - EXIT
    exec {lock_fd}>&-
    if [[ -n $error ]]; then
        fail "$error"
        return 1
    fi
    if [[ $action == stop ]]; then
        send_notification low 'Idle OFF' 'Auto lock/sleep paused.' chronometer
    else
        send_notification low 'Idle ON' 'Auto lock/sleep enabled.' system-suspend
    fi
}

main "$@"
