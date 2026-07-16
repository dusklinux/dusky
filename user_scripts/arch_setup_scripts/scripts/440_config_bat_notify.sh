#!/usr/bin/env bash
# battery notify configurator
#===============================================================================
# BATTERY NOTIFY CONFIGURATION
# Configure battery notification thresholds for battery_notify.sh
#===============================================================================

set -euo pipefail

#===============================================================================
# CONFIGURATION
#===============================================================================
readonly NOTIFY_SCRIPT="${HOME}/user_scripts/battery/notify/battery_notify.sh"
readonly SERVICE_NAME="dusky_battery.service"
readonly SCRIPT_NAME="${0##*/}"

# Sensible defaults for most users
readonly PRESET_FULL=100
readonly PRESET_LOW=20
readonly PRESET_CRITICAL=10

# Gum colors
readonly C_TEXT="212"
readonly C_ACCENT="99"
readonly C_WARN="208"
readonly C_ERR="196"
readonly C_OK="35"

#===============================================================================
# UTILITY FUNCTIONS
#===============================================================================
declare TEMP_FILE=""
declare -i HAS_GUM=0

_check_gum() {
    command -v gum &>/dev/null && HAS_GUM=1 || HAS_GUM=0
}
_check_gum

cleanup() {
    if [[ -n "$TEMP_FILE" && -f "$TEMP_FILE" ]]; then
        rm -f "$TEMP_FILE"
    fi
}
trap cleanup EXIT

die() {
    if ((HAS_GUM)); then
        gum style --foreground "$C_ERR" "✗ Error: $1" >&2
    else
        printf '\033[1;31m✗ Error: %s\033[0m\n' "$1" >&2
    fi
    exit 1
}

info() {
    if ((HAS_GUM)); then
        gum style --foreground "$C_ACCENT" "$1"
    else
        printf '\033[1;34m%s\033[0m\n' "$1"
    fi
}

success() {
    if ((HAS_GUM)); then
        gum style --foreground "$C_OK" "✓ $1"
    else
        printf '\033[1;32m✓ %s\033[0m\n' "$1"
    fi
}

warn() {
    if ((HAS_GUM)); then
        gum style --foreground "$C_WARN" "⚠ $1"
    else
        printf '\033[1;33m⚠ %s\033[0m\n' "$1"
    fi
}

is_valid_percent() {
    [[ -n "${1:-}" && "$1" =~ ^[0-9]+$ && "$1" -ge 1 && "$1" -le 100 ]]
}

#===============================================================================
# BATTERY DETECTION
#===============================================================================
check_battery() {
    # Method 1: upower (preferred)
    if command -v upower &>/dev/null; then
        if upower -e 2>/dev/null | grep -qiE 'BAT|battery'; then
            return 0
        fi
    fi

    # Method 2: sysfs fallback
    local bat_path
    for bat_path in /sys/class/power_supply/BAT*; do
        [[ -d "$bat_path" ]] && return 0
    done

    return 1
}

#===============================================================================
# CONFIG FUNCTIONS
#===============================================================================
get_current_value() {
    local var_name="$1"
    local default="$2"

    if [[ ! -f "$NOTIFY_SCRIPT" ]]; then
        printf '%s' "$default"
        return 0
    fi

    # Anchored regex with negative lookbehind to avoid partial matches
    local value
    value=$(grep -oP "(?<![A-Za-z_])${var_name}:-\K[0-9]+" "$NOTIFY_SCRIPT" 2>/dev/null | head -1) || true
    printf '%s' "${value:-$default}"
}

update_config() {
    local full="$1"
    local low="$2"
    local critical="$3"

    [[ ! -f "$NOTIFY_SCRIPT" ]] && die "Notify script not found: $NOTIFY_SCRIPT"
    [[ ! -w "$NOTIFY_SCRIPT" ]] && die "Notify script not writable: $NOTIFY_SCRIPT"

    TEMP_FILE=$(mktemp) || die "Failed to create temp file"

    # Update all three thresholds in one pass
    if ! sed -e "s/\(BATTERY_FULL_THRESHOLD:-\)[0-9]\+/\1${full}/" \
             -e "s/\(BATTERY_LOW_THRESHOLD:-\)[0-9]\+/\1${low}/" \
             -e "s/\(BATTERY_CRITICAL_THRESHOLD:-\)[0-9]\+/\1${critical}/" \
             "$NOTIFY_SCRIPT" > "$TEMP_FILE"; then
        die "Failed to process configuration with sed"
    fi

    # Validate output
    [[ ! -s "$TEMP_FILE" ]] && die "Generated config is empty - aborting"

    local orig_size new_size
    orig_size=$(wc -c < "$NOTIFY_SCRIPT")
    new_size=$(wc -c < "$TEMP_FILE")

    # Sanity check: file shouldn't shrink dramatically
    ((new_size < orig_size / 2)) && die "Generated file too small - aborting"

    mv -f "$TEMP_FILE" "$NOTIFY_SCRIPT" || die "Failed to update config"
    TEMP_FILE=""  # Clear reference after successful move
    chmod +x "$NOTIFY_SCRIPT"
}

restart_service() {
    # Check if service unit exists
    if ! systemctl --user cat "$SERVICE_NAME" &>/dev/null; then
        info "Service '$SERVICE_NAME' not found - skipping restart"
        return 0
    fi

    if systemctl --user is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        if systemctl --user restart "$SERVICE_NAME" 2>/dev/null; then
            success "Service restarted"
        else
            warn "Failed to restart service"
        fi
    else
        info "Service not running (start with: systemctl --user start $SERVICE_NAME)"
    fi
}

#===============================================================================
# USAGE
#===============================================================================
show_usage() {
    cat << EOF
Usage: ${SCRIPT_NAME} [OPTIONS]

Configure battery notification thresholds.

OPTIONS:
    --default    Apply sensible defaults without TUI:
                   • Full Battery Reminder:    ${PRESET_FULL}%
                   • Low Battery Warning:      ${PRESET_LOW}%
                   • Critical (Auto-Suspend):  ${PRESET_CRITICAL}%
    -h, --help   Show this help message

EXAMPLES:
    ${SCRIPT_NAME}           # Interactive TUI mode
    ${SCRIPT_NAME} --default # Apply defaults non-interactively

NOTE: This script requires a laptop with a battery.
      Config path: ${NOTIFY_SCRIPT}
EOF
}

#===============================================================================
# DEFAULT MODE (Non-Interactive)
#===============================================================================
apply_defaults() {
    printf '\n'
    info "Applying default battery thresholds..."
    printf '\n  Full Battery Reminder:     %s%%\n' "$PRESET_FULL"
    printf '  Low Battery Warning:       %s%%\n' "$PRESET_LOW"
    printf '  Critical (Auto-Suspend):   %s%%\n\n' "$PRESET_CRITICAL"

    update_config "$PRESET_FULL" "$PRESET_LOW" "$PRESET_CRITICAL"
    success "Configuration updated"
    printf '\n'
    restart_service
}

#===============================================================================
# TUI MODE
#===============================================================================
ensure_gum() {
    ((HAS_GUM)) && return 0

    printf 'Error: gum is required for TUI mode.\n'
    read -rn1 -p "Install via pacman? [y/N] " REPLY
    printf '\n'

    if [[ "${REPLY:-n}" =~ ^[Yy]$ ]]; then
        sudo pacman -S --needed --noconfirm gum || die "Failed to install gum"
        _check_gum
        ((HAS_GUM)) || die "gum not found after install"
    else
        die "Use --default for non-interactive mode, or install gum manually"
    fi
}

show_header() {
    gum style --border normal --margin "1" --padding "1 2" --border-foreground "$C_TEXT" \
        "$(gum style --foreground "$C_TEXT" --bold "BATTERY") $(gum style --foreground "$C_ACCENT" "NOTIFICATIONS")"
}

prompt_value() {
    local current="$1"
    local header="$2"
    local result

    while true; do
        result=$(gum input \
            --placeholder "$current" \
            --value "$current" \
            --header "$header" \
            --header.foreground "$C_ACCENT") || {
            printf '%s' "$current"
            return 0
        }

        if is_valid_percent "$result"; then
            printf '%s' "$result"
            return 0
        elif [[ -z "$result" ]]; then
            printf '%s' "$current"
            return 0
        else
            warn "Enter a value between 1 and 100"
            sleep 0.8
        fi
    done
}

run_tui() {
    ensure_gum

    # Must be interactive terminal
    [[ ! -t 0 || ! -t 1 ]] && die "TUI requires interactive terminal. Use --default flag."

    # Load current values
    local CUR_FULL CUR_LOW CUR_CRITICAL
    CUR_FULL=$(get_current_value "BATTERY_FULL_THRESHOLD" "$PRESET_FULL")
    CUR_LOW=$(get_current_value "BATTERY_LOW_THRESHOLD" "$PRESET_LOW")
    CUR_CRITICAL=$(get_current_value "BATTERY_CRITICAL_THRESHOLD" "$PRESET_CRITICAL")

    local NEW_FULL="$CUR_FULL"
    local NEW_LOW="$CUR_LOW"
    local NEW_CRITICAL="$CUR_CRITICAL"
    local choice

    while true; do
        clear
        show_header

        choice=$(gum choose \
            --cursor.foreground="$C_TEXT" \
            --selected.foreground="$C_TEXT" \
            --header "Select a threshold to configure:" \
            "1. Full Battery Reminder     [${NEW_FULL}%]   (notify when charging reaches this)" \
            "2. Low Battery Warning       [${NEW_LOW}%]   (show warning notification)" \
            "3. Critical / Auto-Suspend   [${NEW_CRITICAL}%]   (suspend system to save data)" \
            "────────────────────────────────────────────────" \
            "↺ Reset to Defaults" \
            "▶ Apply Changes & Restart Service" \
            "✗ Exit Without Saving") || {
            info "Cancelled."
            exit 0
        }

        case "$choice" in
            *"Full Battery"*)
                NEW_FULL=$(prompt_value "$NEW_FULL" "Notify when charging reaches (%):")
                ;;
            *"Low Battery"*)
                NEW_LOW=$(prompt_value "$NEW_LOW" "Show low battery warning at (%):")
                ;;
            *"Critical"*)
                NEW_CRITICAL=$(prompt_value "$NEW_CRITICAL" "Auto-suspend when battery reaches (%):")
                ;;
            *"Reset"*)
                NEW_FULL="$PRESET_FULL"
                NEW_LOW="$PRESET_LOW"
                NEW_CRITICAL="$PRESET_CRITICAL"

                printf '\n'
                gum spin --spinner dot --title "Applying defaults..." -- sleep 0.3
                update_config "$NEW_FULL" "$NEW_LOW" "$NEW_CRITICAL"
                success "Defaults applied"
                printf '\n'
                restart_service

                CUR_FULL="$NEW_FULL"
                CUR_LOW="$NEW_LOW"
                CUR_CRITICAL="$NEW_CRITICAL"

                sleep 1.5
                ;;
            *"Apply"*)
                # Validate threshold order: Critical < Low < Full
                local errors=""

                if ((NEW_CRITICAL >= NEW_LOW)); then
                    errors+="  • Critical (${NEW_CRITICAL}%) must be lower than Low (${NEW_LOW}%)\n"
                fi
                if ((NEW_LOW >= NEW_FULL)); then
                    errors+="  • Low (${NEW_LOW}%) must be lower than Full (${NEW_FULL}%)\n"
                fi

                if [[ -n "$errors" ]]; then
                    printf '\n'
                    gum style --border double --border-foreground "$C_WARN" --padding "1" --margin "0 1" \
                        "$(gum style --foreground "$C_WARN" --bold "⚠ INVALID THRESHOLD ORDER")" \
                        "" \
                        "$(printf '%b' "$errors")" \
                        "" \
                        "Expected order: Critical < Low < Full"

                    printf '\n'
                    if ! gum confirm --affirmative="Apply Anyway" --negative="Go Back"; then
                        continue
                    fi
                fi

                # Check for actual changes
                if [[ "$NEW_FULL" == "$CUR_FULL" && \
                      "$NEW_LOW" == "$CUR_LOW" && \
                      "$NEW_CRITICAL" == "$CUR_CRITICAL" ]]; then
                    printf '\n'
                    info "No changes detected."
                    sleep 1
                    continue
                fi

                printf '\n'
                gum spin --spinner dot --title "Updating configuration..." -- sleep 0.3
                update_config "$NEW_FULL" "$NEW_LOW" "$NEW_CRITICAL"
                success "Configuration saved"
                printf '\n'
                restart_service

                CUR_FULL="$NEW_FULL"
                CUR_LOW="$NEW_LOW"
                CUR_CRITICAL="$NEW_CRITICAL"

                printf '\n'
                info "Returning to menu..."
                sleep 1.5
                ;;
            *"Exit"* | *"────"*)
                info "No changes made."
                exit 0
                ;;
        esac
    done
}

#===============================================================================
# MAIN ENTRY POINT
#===============================================================================
main() {

# Battery check - fail fast for desktops
    if ! check_battery; then
        info "No battery detected. Skipping configuration (Desktop detected)."
        exit 0
    fi

    # Verify notify script exists
    if [[ ! -f "$NOTIFY_SCRIPT" ]]; then
        die "Battery notify script not found at: $NOTIFY_SCRIPT

Please ensure the battery notification system is installed first."
    fi

    # Parse arguments
    case "${1:-}" in
        --default)
            apply_defaults
            ;;
        -h|--help)
            show_usage
            ;;
        "")
            run_tui
            ;;
        *)
            die "Unknown option: $1 (use --help for usage)"
            ;;
    esac
}

main "$@"
