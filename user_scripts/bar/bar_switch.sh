#!/usr/bin/env bash
# =============================================================================
# bar_switch.sh - Unified bar switcher: Waybar <-> Adaptive Glass
# =============================================================================
# Usage:
#   bar_switch.sh                # Toggle between the two bars
#   bar_switch.sh toggle         # Toggle between the two bars
#   bar_switch.sh start          # Start the saved/default bar without toggling
#   bar_switch.sh waybar         # Switch to Waybar unconditionally
#   bar_switch.sh adaptive-glass # Switch to Adaptive Glass unconditionally
#   bar_switch.sh status         # Print the currently active bar
#   bar_switch.sh --help         # Show this help text
#
# State is stored in: ~/.config/dusky/settings/active_bar
#   - file contains either "waybar" or "adaptive-glass"
#   - absent = default to Waybar
#
# Integration points:
#   - autostart.lua: call "bar_switch.sh start" instead of waybar_toggle.sh
#   - keybinds.lua: bind a key to "bar_switch.sh toggle" for quick toggle
#   - waybar_toggle.sh remains the Waybar fallback launcher.
# =============================================================================

set -euo pipefail

readonly STATE_FILE="${HOME}/.config/dusky/settings/active_bar"
readonly WAYBAR_TOGGLE="${HOME}/user_scripts/waybar/waybar_toggle.sh"
readonly ADAPTIVE_INSTANCE="dusky-adaptive-glass"
readonly ADAPTIVE_ENTRY="${HOME}/.config/ags/app.tsx"
readonly ADAPTIVE_LOG="${XDG_RUNTIME_DIR:-/tmp}/dusky-adaptive-glass.log"
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/bar_switch.lock"
readonly WAYBAR_SETTLE_SEC="2"
readonly WAYBAR_UNIT="waybar-adaptive-glass"

if [[ -t 2 ]]; then
    C_INFO='\033[0;34m'; C_OK='\033[0;32m'; C_WARN='\033[0;33m'
    C_ERR='\033[0;31m'; C_RST='\033[0m'; C_BOLD='\033[1m'
else
    C_INFO=''; C_OK=''; C_WARN=''; C_ERR=''; C_RST=''; C_BOLD=''
fi

log_info() { printf "${C_INFO}[INFO]${C_RST} %s\n" "$*" >&2; }
log_ok() { printf "${C_OK}[OK]${C_RST} %s\n" "$*" >&2; }
log_warn() { printf "${C_WARN}[WARN]${C_RST} %s\n" "$*" >&2; }
log_err() { printf "${C_ERR}[ERROR]${C_RST} %s\n" "$*" >&2; }

save_state() {
    mkdir -p "$(dirname "$STATE_FILE")"
    printf '%s\n' "$1" > "$STATE_FILE"
}

active_bar() {
    local current

    if [[ -f "$STATE_FILE" ]]; then
        current="$(tr -d '[:space:]' < "$STATE_FILE")"
    else
        current="waybar"
    fi

    case "$current" in
        waybar|adaptive-glass)
            printf '%s\n' "$current"
            ;;
        novabar)
            save_state "adaptive-glass"
            printf '%s\n' "adaptive-glass"
            ;;
        *)
            log_warn "Unknown saved bar '$current'; falling back to waybar."
            save_state "waybar"
            printf '%s\n' "waybar"
            ;;
    esac
}

is_waybar_running() {
    pgrep -x "waybar" >/dev/null 2>&1
}

is_adaptive_glass_running() {
    ags list 2>/dev/null | grep -Fx "$ADAPTIVE_INSTANCE" >/dev/null 2>&1
}

wait_for_waybar() {
    local attempts="${1:-12}"
    local i

    for (( i = 0; i < attempts; i++ )); do
        is_waybar_running && return 0
        sleep 0.1
    done

    return 1
}

waybar_stayed_running() {
    wait_for_waybar || return 1
    sleep "$WAYBAR_SETTLE_SEC"
    is_waybar_running
}

stop_waybar() {
    if is_waybar_running; then
        log_info "Stopping Waybar..."
        pkill -x "waybar" >/dev/null 2>&1 || true
        sleep 0.4
        log_ok "Waybar stopped."
    fi
}

stop_adaptive_glass() {
    log_info "Stopping Adaptive Glass..."
    ags quit --instance "$ADAPTIVE_INSTANCE" >/dev/null 2>&1 || true
    sleep 0.4
    log_ok "Adaptive Glass stopped."
}

start_waybar_direct() {
    if ! command -v waybar >/dev/null 2>&1; then
        log_err "waybar command not found."
        return 1
    fi

    log_info "Starting Waybar directly..."
    (
        exec 9>&-
        unset XDG_ACTIVATION_TOKEN DESKTOP_STARTUP_ID
        setsid waybar </dev/null >/dev/null 2>&1 &
    )
}

start_waybar_service() {
    if ! command -v systemd-run >/dev/null 2>&1; then
        return 1
    fi

    log_info "Starting Waybar as a user service..."
    systemd-run --user --quiet --collect --unit="$WAYBAR_UNIT" -- waybar
}

start_waybar() {
    if is_waybar_running; then
        log_ok "Waybar is already running."
        return 0
    fi

    if start_waybar_service; then
        if waybar_stayed_running; then
            log_ok "Waybar launched."
            return 0
        fi
        log_warn "systemd-run did not leave a stable Waybar process; falling back."
    fi

    if [[ -f "$WAYBAR_TOGGLE" ]]; then
        log_info "Starting Waybar via waybar_toggle.sh..."
        if (
            exec 9>&-
            bash "$WAYBAR_TOGGLE" --on
        ); then
            if waybar_stayed_running; then
                log_ok "Waybar launched."
                return 0
            fi
            log_warn "waybar_toggle.sh did not leave a stable Waybar process; falling back."
        else
            log_warn "waybar_toggle.sh failed; falling back."
        fi
    fi

    start_waybar_direct
    if waybar_stayed_running; then
        log_ok "Waybar launched."
        return 0
    fi

    log_err "Waybar failed to stay running."
    return 1
}

start_adaptive_glass() {
    if ! command -v ags >/dev/null 2>&1; then
        log_err "ags command not found."
        return 1
    fi

    if ! [[ -f "$ADAPTIVE_ENTRY" ]]; then
        log_err "Adaptive Glass entry not found: $ADAPTIVE_ENTRY"
        return 1
    fi

    if is_adaptive_glass_running; then
        log_ok "Adaptive Glass is already running."
        return 0
    fi

    log_info "Starting Adaptive Glass..."
    (
        exec 9>&-
        setsid ags run "$ADAPTIVE_ENTRY" </dev/null >"$ADAPTIVE_LOG" 2>&1 &
    )
    log_ok "Adaptive Glass launched."
}

show_help() {
    cat <<EOF
${C_BOLD}bar_switch.sh${C_RST} - Switch between Waybar and Adaptive Glass

  ${C_INFO}Usage:${C_RST}
    bar_switch.sh                  Toggle active bar
    bar_switch.sh toggle           Toggle active bar
    bar_switch.sh start            Start saved/default bar without toggling
    bar_switch.sh waybar           Force switch to Waybar
    bar_switch.sh adaptive-glass   Force switch to Adaptive Glass
    bar_switch.sh status           Print currently active bar
    bar_switch.sh --help / -h      Show this message

  ${C_INFO}State file:${C_RST}  $STATE_FILE
  ${C_INFO}Active bar:${C_RST}  $(active_bar)
EOF
}

exec 9>"$LOCK_FILE"
if ! flock -w 5 9; then
    log_err "Another bar_switch instance is running (lock: $LOCK_FILE)"
    exit 1
fi

TARGET="${1:-toggle}"

case "$TARGET" in
    --help|-h|help)
        show_help
        exit 0
        ;;
    status)
        ACTIVE="$(active_bar)"
        printf '%s\n' "$ACTIVE"
        if [[ "$ACTIVE" == "waybar" ]]; then
            is_waybar_running && printf '  running: yes\n' || printf '  running: no\n'
        else
            is_adaptive_glass_running && printf '  running: yes\n' || printf '  running: no\n'
        fi
        exit 0
        ;;
    start)
        TARGET="$(active_bar)"
        log_info "Start saved bar: $TARGET"
        ;;
    toggle)
        CURRENT="$(active_bar)"
        if [[ "$CURRENT" == "adaptive-glass" ]]; then
            TARGET="waybar"
        else
            TARGET="adaptive-glass"
        fi
        log_info "Toggle: $CURRENT -> $TARGET"
        ;;
    ags|adaptive)
        TARGET="adaptive-glass"
        ;;
    waybar|adaptive-glass)
        ;;
    *)
        log_err "Unknown argument: '$TARGET'. Use --help for usage."
        exit 1
        ;;
esac

if [[ "$TARGET" == "adaptive-glass" ]]; then
    stop_waybar
    save_state "adaptive-glass"
    start_adaptive_glass
    log_ok "Active bar -> ${C_BOLD}Adaptive Glass${C_RST}"
    notify-send -a "Bar Switch" -i preferences-desktop "Switched to Adaptive Glass" \
        "Adaptive Glass is now your status bar." 2>/dev/null || true
else
    stop_adaptive_glass
    stop_waybar
    save_state "waybar"
    start_waybar
    log_ok "Active bar -> ${C_BOLD}Waybar${C_RST}"
    notify-send -a "Bar Switch" -i preferences-desktop "Switched to Waybar" \
        "Waybar is now your status bar." 2>/dev/null || true
fi
