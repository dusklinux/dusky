#!/usr/bin/env bash
# Atomically resets Waypaper configuration because of the github restore permission bug
# -----------------------------------------------------------------------------
# Script: reset_waypaper.sh
# Description: Atomically resets Waypaper configuration for Arch/Hyprland/UWSM.
# -----------------------------------------------------------------------------

# 1. Strict Mode & Safety
# -e: Exit immediately on error
# -u: Treat unset variables as an error
# -o pipefail: Catch errors in piped commands
set -euo pipefail

# 2. Environment & Constants
# Respect XDG Base Directory specification, fallback to ~/.config
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/waypaper"
readonly CONFIG_FILE="$CONFIG_DIR/config.ini"

# ANSI Colors for feedback
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_RED=$'\033[1;31m'
readonly C_RESET=$'\033[0m'

# 3. Utility Functions
log_info() { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$1"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET} %s\n" "$1"; }
log_err() { printf "${C_RED}[ERROR]${C_RESET} %s\n" "$1" >&2; }

# Trap for cleanup/error handling
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_err "Script failed with exit code $exit_code."
    fi
}
trap cleanup EXIT

# 4. Main Execution
main() {
    log_info "Initializing Waypaper configuration reset..."

    # Ensure configuration directory exists
    if [[ ! -d "$CONFIG_DIR" ]]; then
        mkdir -p "$CONFIG_DIR"
        log_info "Created configuration directory: $CONFIG_DIR"
    fi

    # Remove existing config file (force delete, no error if missing)
    if [[ -f "$CONFIG_FILE" ]]; then
        rm -f "$CONFIG_FILE"
        log_info "Removed old configuration."
    fi

    # Write new configuration
    # We use a quoted heredoc (<<'EOF') to prevent shell expansion of $wallpaper
    cat > "$CONFIG_FILE" <<'EOF'
[Settings]
language = en
folder = ~/Pictures/wallpapers
monitors = All
wallpaper = ~/Pictures/wallpapers/dusk_default.jpg
show_path_in_tooltip = True
backend = swww
fill = fill
sort = name
color = #ffffff
subfolders = False
all_subfolders = False
show_hidden = False
show_gifs_only = False
zen_mode = False
post_command = ~/user_scripts/theme_matugen/theme_ctl.sh refresh
number_of_columns = 3
swww_transition_type = any
swww_transition_step = 63
swww_transition_angle = 0
swww_transition_duration = 2
swww_transition_fps = 60
mpvpaper_sound = False
mpvpaper_options = 
use_xdg_state = False
stylesheet = ~/.config/waypaper/style.css
EOF

    log_success "New configuration generated at: $CONFIG_FILE"
    
    # Optional: Logic verification for the 'dusk' user path
    if [[ "$USER" != "dusk" ]]; then
         printf "${C_BLUE}[NOTE]${C_RESET} Config contains hardcoded path '/home/dusk/...'. Ensure this is correct for your user.\n"
    fi
}

main
