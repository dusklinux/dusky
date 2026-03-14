#!/usr/bin/env bash
# ==============================================================================
# ARCH LINUX :: UWSM :: MATUGEN ROFI MENU
# ==============================================================================
# Description: Interactive Rofi interface for theme_ctl.sh.
#              - UWSM compliant
#              - Bash arrays for menu data
#              - Journal logging
#              - Reliable error handling
#              - Fixed-choice menu input only

set -Eeuo pipefail
shopt -s inherit_errexit

# --- CONFIGURATION ---
readonly THEME_CTL="dusky-theme-ctl"
readonly APP_NAME="matugen-menu"
readonly ROFI_THEME_STR='window { width: 400px; }'

# --- REQUIRED COMMANDS ---
readonly -a REQUIRED_CMDS=(
    uwsm-app
    rofi
)

# --- OPTIONAL COMMANDS ---
# These are used only for diagnostics. The script still runs without them.
readonly -a OPTIONAL_CMDS=(
    logger
    notify-send
)

# --- MENU DATA ---
readonly -a OPTS_MODE=(
    dark
    light
)

readonly -a OPTS_SCHEME=(
    disable
    scheme-tonal-spot
    scheme-vibrant
    scheme-fruit-salad
    scheme-expressive
    scheme-fidelity
    scheme-rainbow
    scheme-neutral
    scheme-monochrome
    scheme-content
)

readonly -a OPTS_CONTRAST=(
    disable
    -1.0
    -0.8
    -0.6
    -0.4
    -0.2
     0.2
     0.4
     0.6
     0.8
     1.0
)

# New Matugen V2+ parameter arrays
readonly -a OPTS_INDEX=(
    0
    1
    2
    3
)

readonly -a OPTS_BASE16=(
    disable
    wal
)

readonly -a ROFI_CMD=(
    uwsm-app
    --
    rofi
    -dmenu
    -i
    -no-custom
    -format
    s
)

# --- HELPERS ---
have_cmd() {
    command -v "$1" >/dev/null 2>&1
}

log_journal() {
    local priority=$1
    local message=$2

    have_cmd logger || return 0
    logger -p "user.${priority}" -t "$APP_NAME" -- "$message" >/dev/null 2>&1 || return 0
}

log_info() {
    log_journal info "$1"
}

log_error() {
    log_journal err "$1"
}

notify_critical() {
    local title=$1
    local body=$2

    have_cmd notify-send || return 0
    notify-send -u critical -- "$title" "$body" >/dev/null 2>&1 || return 0
}

fatal() {
    local log_message=$1
    local notify_message=${2:-$1}

    log_error "$log_message"
    notify_critical "Theme Menu Error" "$notify_message"
    exit 1
}

on_unexpected_error() {
    local exit_code=$1
    local line_no=$2

    log_error "Unhandled error at line ${line_no} (exit ${exit_code})."
    notify_critical "Theme Menu Error" "Unexpected failure. Check logs."
    exit "$exit_code"
}

trap 'on_unexpected_error $? $LINENO' ERR

require_commands() {
    local cmd

    for cmd in "${REQUIRED_CMDS[@]}"; do
        have_cmd "$cmd" || fatal "Missing required command: $cmd" "Missing dependency: $cmd"
    done
}

validate_controller() {
    command -v "$THEME_CTL" >/dev/null 2>&1 || fatal \
        "Controller script missing or not executable: $THEME_CTL" \
        "Controller script missing."
}

array_contains() {
    local needle=$1
    local -n haystack=$2
    local item

    for item in "${haystack[@]}"; do
        [[ $item == "$needle" ]] && return 0
    done

    return 1
}

is_rofi_abort_exit() {
    local exit_code=$1

    [[ $exit_code -eq 1 || $exit_code -eq 130 || $exit_code -eq 143 ]] && return 0
    (( exit_code >= 10 && exit_code <= 28 ))
}

run_menu() {
    local prompt=$1
    local options_name=$2
    local output_name=$3

    local -n options_ref=$options_name
    local -n output_ref=$output_name

    local selected=""
    local exit_code=0

    selected=$(
        printf '%s\n' "${options_ref[@]}" |
            "${ROFI_CMD[@]}" -p "$prompt" -theme-str "$ROFI_THEME_STR"
    ) || exit_code=$?

    if [[ $exit_code -eq 0 ]]; then
        if [[ -z $selected ]]; then
            fatal "Empty selection returned for '$prompt'" "Invalid selection received. Check logs."
        fi

        if ! array_contains "$selected" "$options_name"; then
            fatal \
                "Invalid selection returned for '$prompt': $selected" \
                "Invalid selection received. Check logs."
        fi

        output_ref=$selected
        return 0
    fi

    if is_rofi_abort_exit "$exit_code"; then
        log_info "Rofi closed at '$prompt' with exit code $exit_code."
        return 1
    fi

    fatal "Rofi failed at '$prompt' with exit code $exit_code" "Rofi failed. Check logs."
}

main() {
    local selected_mode=""
    local selected_type=""
    local selected_contrast=""
    local selected_index=""
    local selected_base16=""

    require_commands
    validate_controller

    run_menu "Mode" OPTS_MODE selected_mode || return 0
    run_menu "Scheme" OPTS_SCHEME selected_type || return 0
    run_menu "Contrast" OPTS_CONTRAST selected_contrast || return 0
    
    # New Matugen V2 prompts
    run_menu "Color Index (0=Primary)" OPTS_INDEX selected_index || return 0
    run_menu "Base16 Backend" OPTS_BASE16 selected_base16 || return 0

    log_info "Applying settings: Mode=$selected_mode, Type=$selected_type, Contrast=$selected_contrast, Index=$selected_index, Base16=$selected_base16"

    if ! "$THEME_CTL" set \
        --no-wall \
        --mode "$selected_mode" \
        --type "$selected_type" \
        --contrast "$selected_contrast" \
        --index "$selected_index" \
        --base16 "$selected_base16"; then
        fatal "Failed to apply theme settings via $THEME_CTL" "Failed to apply changes. Check logs."
    fi

    log_info "Theme settings applied successfully."
}

main "$@"
