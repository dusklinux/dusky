#!/usr/bin/env bash
# ==============================================================================
# ARCH LINUX :: UWSM :: MATUGEN ROFI MENU
# ==============================================================================
# Description: Interactive Rofi interface for theme_ctl.sh.
#              - UWSM Compliant (Wraps Rofi)
#              - Bash Arrays for Data
#              - System Journal Logging
#              - Robust Exit Handling
#              - "Trust Me" Execution (Ignores minor exit code failures)

set -euo pipefail

# --- CONFIGURATION ---
readonly THEME_CTL="${HOME}/user_scripts/theme_matugen/theme_ctl.sh"
readonly APP_NAME="matugen-menu"

# --- DEPENDENCIES ---
readonly DEPS=(uwsm-app rofi notify-send)

for cmd in "${DEPS[@]}"; do
    if ! command -v "$cmd" &>/dev/null; then
        logger -p user.err -t "$APP_NAME" "CRITICAL: Required command '$cmd' not found."
        notify-send -u critical "Theme Menu Error" "Missing dependency: $cmd"
        exit 1
    fi
done

if [[ ! -x "$THEME_CTL" ]]; then
    logger -p user.err -t "$APP_NAME" "CRITICAL: Controller not found or not executable at $THEME_CTL"
    notify-send -u critical "Theme Menu Error" "Controller script missing."
    exit 1
fi

# --- DATA STRUCTURES ---
readonly OPTS_MODE=("dark" "light")

readonly OPTS_SCHEME=(
    "disable"
    "scheme-tonal-spot"
    "scheme-vibrant"
    "scheme-fruit-salad"
    "scheme-expressive"
    "scheme-fidelity"
    "scheme-rainbow"
    "scheme-neutral"
    "scheme-monochrome"
    "scheme-content"
)

readonly OPTS_CONTRAST=(
    "disable"
    "0.5"
    "1.0"
    "-0.5"
    "-1.0"
)

# --- FUNCTIONS ---

log_info() {
    logger -p user.info -t "$APP_NAME" "$1"
}

run_menu() {
    local prompt="$1"
    shift
    local -a options=("${@}")
    local selected
    local exit_code=0

    selected=$(printf '%s\n' "${options[@]}" | uwsm-app -- rofi -dmenu -i -p "$prompt" -theme-str 'window {width: 400px;}') || exit_code=$?

    if [[ $exit_code -eq 0 ]]; then
        printf '%s' "$selected"
    elif [[ $exit_code -eq 1 ]]; then
        # User Cancelled
        log_info "User cancelled selection at '$prompt'."
        exit 0
    else
        logger -p user.err -t "$APP_NAME" "Rofi crashed with exit code $exit_code"
        notify-send -u critical "Theme Menu Error" "Rofi crashed (Code $exit_code)"
        exit 1
    fi
}

# --- EXECUTION FLOW ---

# 1. Select Mode
selected_mode=$(run_menu "Mode" "${OPTS_MODE[@]}")
[[ -z "$selected_mode" ]] && exit 0

# 2. Select Scheme
selected_type=$(run_menu "Scheme" "${OPTS_SCHEME[@]}")
[[ -z "$selected_type" ]] && exit 0

# 3. Select Contrast
selected_contrast=$(run_menu "Contrast" "${OPTS_CONTRAST[@]}")
[[ -z "$selected_contrast" ]] && exit 0

# --- APPLY CHANGES ---

log_info "Applying settings: Mode=$selected_mode, Type=$selected_type, Contrast=$selected_contrast"

# Run controller but suppress script failure.
# We capture the exit code for logging purposes only.
# --- APPLY CHANGES ---

log_info "Applying settings: Mode=$selected_mode, Type=$selected_type, Contrast=$selected_contrast"

if ! "$THEME_CTL" set --no-wall --mode "$selected_mode" --type "$selected_type" --contrast "$selected_contrast"; then
    logger -p user.err -t "$APP_NAME" "Failed to apply theme settings via $THEME_CTL"
    notify-send -u critical "Theme Menu Error" "Failed to apply changes. Check logs."
    exit 1
fi

exit 0
