#!/usr/bin/env bash
# Rofi Power Menu for Hyprland + UWSM
# Arch Linux + Bash 5.3+

set -euo pipefail
shopt -s inherit_errexit

: "${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is not set}"

readonly LOCK_FILE="${XDG_RUNTIME_DIR}/rofi-power.lock"
readonly ACTION_DELAY='0.05'

exec {lock_fd}> "${LOCK_FILE}"
flock -n "${lock_fd}" || exit 0

release_lock() {
    exec {lock_fd}>&- 2>/dev/null || :
}
trap release_lock EXIT

declare -Ar ICONS=(
    [shutdown]=""
    [reboot]=""
    [suspend]=""
    [soft_reboot]=""
    [logout]=""
    [lock]=""
    [cancel]=""
)

declare -Ar LABELS=(
    [lock]="Lock"
    [logout]="Logout"
    [suspend]="Suspend"
    [reboot]="Reboot"
    [soft_reboot]="Soft Reboot"
    [shutdown]="Shutdown"
)

declare -ar ORDER=(
    shutdown
    reboot
    suspend
    lock
    logout
    soft_reboot
)

declare -Ar CONFIRM=(
    [shutdown]=1
    [reboot]=1
    [logout]=1
    [soft_reboot]=1
)

print_entry() {
    local key=$1
    printf '%s  %s\0info\x1f%s\n' "${ICONS[$key]}" "${LABELS[$key]}" "$key"
}

show_main_menu() {
    local uptime_str

    uptime_str=$(LC_ALL=C uptime -p)
    uptime_str=${uptime_str#up }

    printf '\0prompt\x1fUptime\n'
    printf '\0theme\x1fentry { placeholder: "%s"; }\n' "$uptime_str"

    local key
    for key in "${ORDER[@]}"; do
        print_entry "$key"
    done
}

show_confirm_menu() {
    local key=$1
    local label=${LABELS[$key]}

    printf '\0prompt\x1f%s?\n' "$label"
    printf 'Yes, %s\0info\x1f%s:confirmed\n' "$label" "$key"
    printf '%s  No, Cancel\0info\x1fcancel\n' "${ICONS[cancel]}"
}

execute() {
    local action=$1

    release_lock

    case $action in
        lock)
            if ! pgrep -x -u "$UID" hyprlock >/dev/null; then
                {
                    sleep "${ACTION_DELAY}"
                    exec uwsm-app -- hyprlock
                } </dev/null >/dev/null 2>&1 &
            fi
            ;;
        logout)
            sleep "${ACTION_DELAY}"
            exec uwsm stop
            ;;
        suspend)
            sleep "${ACTION_DELAY}"
            exec systemctl suspend
            ;;
        reboot)
            sleep "${ACTION_DELAY}"
            exec systemctl reboot
            ;;
        soft_reboot)
            sleep "${ACTION_DELAY}"
            exec systemctl soft-reboot
            ;;
        shutdown)
            sleep "${ACTION_DELAY}"
            exec systemctl poweroff
            ;;
        *)
            exit 1
            ;;
    esac
}

rofi_info=${ROFI_INFO-}
key=${rofi_info%%:*}
state=
[[ $rofi_info == *:* ]] && state=${rofi_info#*:}

if [[ -z $key ]]; then
    show_main_menu
    exit 0
fi

[[ $key == cancel ]] && exit 0
[[ -v "LABELS[$key]" ]] || exit 1

if [[ $state == confirmed ]]; then
    execute "$key"
    exit 0
fi

if [[ -v "CONFIRM[$key]" ]]; then
    show_confirm_menu "$key"
    exit 0
fi

execute "$key"
