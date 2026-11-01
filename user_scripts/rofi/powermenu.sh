#!/usr/bin/env bash
# Rofi Power Menu for Hyprland + UWSM
# Arch Linux + Bash 5.3+

set -euo pipefail
shopt -s inherit_errexit

if [[ "${ROFI_RETV-}" == "" && "${1-}" =~ ^(-h|--help|help)$ ]]; then
    cat <<'EOF'
Usage: powermenu.sh
  Launched by rofi as a script-mode modi, e.g.:
    rofi -show power-menu -modi "power-menu:/path/to/powermenu.sh" -no-fixed-num-lines -i
EOF
    exit 0
fi

: "${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is not set}"

readonly LOCK_FILE="${XDG_RUNTIME_DIR}/rofi-power.lock"
readonly ACTION_DELAY='0.05'
readonly SESSION_SCRIPT="$HOME/user_scripts/wlogout/dusky_session.sh"

readonly THEME='window { width: 340px; padding: 16px; } mainbox { spacing: 12px; } inputbar { padding: 10px 12px; } listview { lines: 7; fixed-height: false; dynamic: true; spacing: 6px; } element { padding: 8px 12px; } entry { placeholder: "Filter…"; }'

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
    [hibernate]=""
    [soft_reboot]=""
    [logout]=""
    [lock]=""
    [cancel]=""
    [confirm_yes]=""
)

declare -Ar LABELS=(
    [lock]="Lock"
    [logout]="Logout"
    [suspend]="Suspend"
    [hibernate]="Hibernate"
    [reboot]="Reboot"
    [soft_reboot]="Soft Reboot"
    [shutdown]="Shutdown"
)

declare -Ar KEYWORDS=(
    [shutdown]="power off halt"
    [reboot]="restart"
    [suspend]="sleep"
    [hibernate]="sleep disk freeze"
    [soft_reboot]="restart soft reload"
    [logout]="log out exit quit"
    [lock]="lock screen secure"
)

declare -ar ORDER=(
    shutdown
    reboot
    suspend
    hibernate
    lock
    logout
    soft_reboot
)

declare -Ar CONFIRM=(
    [shutdown]=1
    [reboot]=1
    [hibernate]=1
    [logout]=1
    [soft_reboot]=1
)

get_uptime() {
    local uptime_str
    if uptime_str=$(LC_ALL=C uptime -p 2>/dev/null); then
        uptime_str=${uptime_str#up }
        printf '%s' "$uptime_str"
    else
        printf 'session active'
    fi
}

print_entry() {
    local key=$1
    printf '%s\0display\x1f%s  %s\x1finfo\x1f%s\x1fmeta\x1f%s\n' \
        "${LABELS[$key]}" "${ICONS[$key]}" "${LABELS[$key]}" "$key" "${KEYWORDS[$key]}"
}

show_main_menu() {
    local uptime_str
    uptime_str=$(get_uptime)

    printf '\0prompt\x1f%s  Power\n' "${ICONS[shutdown]}"
    printf '\0message\x1fUptime: %s\n' "$uptime_str"
    printf '\0no-custom\x1ftrue\n'
    printf '\0markup-rows\x1ffalse\n'
    printf '\0theme\x1f%s\n' "$THEME"

    local key
    for key in "${ORDER[@]}"; do
        print_entry "$key"
    done
}

show_confirm_menu() {
    local key=$1
    local label=${LABELS[$key]}

    printf '\0prompt\x1f%s?\n' "$label"
    printf '\0message\x1fAre you sure you want to proceed?\n'
    printf '\0no-custom\x1ftrue\n'
    printf '\0markup-rows\x1ffalse\n'
    printf '\0theme\x1f%s\n' "$THEME"

    printf '%s\0display\x1f%s  Yes, %s\x1finfo\x1f%s:confirmed\n' \
        "Yes, $label" "${ICONS[confirm_yes]}" "$label" "$key"
    printf '%s\0display\x1f%s  No, Cancel\x1finfo\x1fcancel\n' \
        "No, Cancel" "${ICONS[cancel]}"
}

execute() {
    local action=$1

    release_lock

    case $action in
        lock)
            if ! pgrep -x -u "$UID" hyprlock >/dev/null; then
                {
                    sleep "${ACTION_DELAY}"
                    exec dusky-run -- hyprlock
                } </dev/null >/dev/null 2>&1 &
            fi
            ;;
        logout)
            sleep "${ACTION_DELAY}"
            exec "$SESSION_SCRIPT" logout
            ;;
        suspend)
            sleep "${ACTION_DELAY}"
            exec systemctl suspend
            ;;
        hibernate)
            sleep "${ACTION_DELAY}"
            exec systemctl hibernate
            ;;
        reboot)
            sleep "${ACTION_DELAY}"
            exec "$SESSION_SCRIPT" reboot
            ;;
        soft_reboot)
            sleep "${ACTION_DELAY}"
            exec "$SESSION_SCRIPT" soft-reboot
            ;;
        shutdown)
            sleep "${ACTION_DELAY}"
            exec "$SESSION_SCRIPT" poweroff
            ;;
        *)
            exit 1
            ;;
    esac
}

rofi_retv=${ROFI_RETV-0}
rofi_info=${ROFI_INFO-}

if [[ $rofi_retv == 2 ]]; then
    show_main_menu
    exit 0
fi

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
