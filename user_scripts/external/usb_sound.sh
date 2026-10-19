#!/usr/bin/env bash
# Dispatch USB sounds to the active local Wayland session (systemd 262+).
set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin"
readonly LOG_TAG="usb-sound"

# Define primary and fallback audio targets
readonly SOUND_CONNECT_PRIMARY="/usr/share/sounds/freedesktop/stereo/device-added.oga"
readonly SOUND_CONNECT_FALLBACK="/usr/share/sounds/freedesktop/stereo/dialog-information.oga"
readonly SOUND_DISCONNECT_PRIMARY="/usr/share/sounds/freedesktop/stereo/device-removed.oga"
readonly SOUND_DISCONNECT_FALLBACK="/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"

resolve_sound() {
    local file
    for file in "$@"; do
        if [[ -f "$file" && -r "$file" ]]; then
            printf '%s' "$file"
            return 0
        fi
    done
    return 1
}

log_error() { logger -t "$LOG_TAG" -p user.err -- "ERROR: $*"; }

get_active_user() {
    # udev/logind use seat0 when the device has no explicit seat assignment.
    local seat="${ID_SEAT:-seat0}" sid properties key value
    local active='' remote='' type='' class='' session_seat='' user_name=''

    sid=$(loginctl show-seat "$seat" --property=ActiveSession --value) || return 1
    [[ -n "$sid" ]] || return 1
    properties=$(loginctl show-session "$sid" \
        --property=Active --property=Remote --property=Type \
        --property=Class --property=Seat --property=Name) || return 1

    while IFS='=' read -r key value; do
        case "$key" in
            Active) active=$value ;;
            Remote) remote=$value ;;
            Type) type=$value ;;
            Class) class=$value ;;
            Seat) session_seat=$value ;;
            Name) user_name=$value ;;
        esac
    done <<< "$properties"

    [[ "$active" == yes && "$remote" == no && "$type" == wayland &&
       "$class" == user && "$session_seat" == "$seat" && -n "$user_name" ]] || return 1
    printf '%s' "$user_name"
}

main() {
    local action="${1:-}"
    local target_user sound_file passwd_entry user_home

    if (( $# != 1 )); then
        printf 'Usage: %s <connect|disconnect>\n' "${0##*/}" >&2
        return 1
    fi

    case "$action" in
        connect|disconnect)
            ;;
        -h|--help)
            echo "Usage: ${0##*/} <connect|disconnect>"
            exit 0
            ;;
        *)
            echo "Error: Invalid or missing action." >&2
            echo "Usage: ${0##*/} <connect|disconnect>" >&2
            exit 1
            ;;
    esac

    if ! target_user=$(get_active_user); then
        return 0
    fi

    if ! passwd_entry=$(getent passwd "$target_user"); then
        log_error "Cannot resolve home directory for $target_user."
        return 1
    fi
    IFS=: read -r _ _ _ _ _ user_home _ <<< "$passwd_entry"
    if [[ "$user_home" != /* ]]; then
        log_error "Invalid home directory for $target_user."
        return 1
    fi
    [[ -f "${user_home}/.config/dusky/settings/usb_udev_toggle" ]] || return 0

    case "$action" in
        connect) sound_file=$(resolve_sound "$SOUND_CONNECT_PRIMARY" "$SOUND_CONNECT_FALLBACK") ;;
        disconnect) sound_file=$(resolve_sound "$SOUND_DISCONNECT_PRIMARY" "$SOUND_DISCONNECT_FALLBACK") ;;
    esac || {
        log_error "No readable $action sound files found."
        return 1
    }

    if [[ ! -x /usr/bin/pw-play ]]; then
        log_error "/usr/bin/pw-play is not installed or executable."
        return 1
    fi

    # Playback belongs to the user manager; udev must not wait for the sound.
    if ! systemd-run --machine="${target_user}@.host" --user --quiet --collect \
        --no-block --no-ask-password --expand-environment=no --service-type=exec \
        --property=RuntimeMaxSec=15s \
        --description="USB Audio ${action}" \
        /usr/bin/pw-play --media-role=Notification "$sound_file"; then
        log_error "Failed to dispatch $action sound to $target_user."
        return 1
    fi

    return 0
}

main "$@"
