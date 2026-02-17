#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# CONFIG
# ============================================================
WALL_DIR="$HOME/Pictures/wallpapers"
SCRIPT_DIR="$HOME/user_scripts"
MATUGEN_BIN="matugen"
WAYBAR_AUTOSTART="$SCRIPT_DIR/waybar/waybar_autostart.sh"

# State file: exists = waybar was running before theme change
WAYBAR_STATE_FILE="${XDG_RUNTIME_DIR:-/tmp}/waybar_was_running"

# ============================================================
# COLORS
# ============================================================
RED='\033[1;31m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
BLUE='\033[1;34m'
RESET='\033[0m'
log()  { echo -e "${BLUE}[theme_ctl]${RESET} $1"; }
ok()   { echo -e "${GREEN}[OK]${RESET} $1"; }
warn() { echo -e "${YELLOW}[WARN]${RESET} $1"; }
err()  { echo -e "${RED}[ERR]${RESET} $1"; }

# ============================================================
# WAYBAR STATE DETECTION
# ============================================================
detect_waybar_state() {
    rm -f "$WAYBAR_STATE_FILE"

    if pgrep -x waybar >/dev/null 2>&1; then
        touch "$WAYBAR_STATE_FILE"
        log "Waybar was running before theme change"
    else
        log "Waybar was NOT running before theme change"
    fi
}

restore_waybar_state() {
    if [[ -f "$WAYBAR_STATE_FILE" ]]; then
        log "Restoring Waybar (was previously running)"
        rm -f "$WAYBAR_STATE_FILE"
        if [[ -x "$WAYBAR_AUTOSTART" ]]; then
            "$WAYBAR_AUTOSTART" &
        else
            warn "Waybar autostart script not found or not executable: $WAYBAR_AUTOSTART"
        fi
    else
        log "Keeping Waybar stopped (preserving hidden state)"
    fi
}

# ============================================================
# WALLPAPER FUNCTIONS
# ============================================================
get_random_wallpaper() {
    find "$WALL_DIR" -type f \( -iname '*.jpg' -o -iname '*.png' -o -iname '*.jpeg' \) | shuf -n 1
}

set_wallpaper() {
    local img="$1"
    log "Wallpaper → $(basename "$img")"
    swww img "$img" --transition-type grow --transition-duration 1 --transition-fps 60
}

generate_colors() {
    local img="$1"
    log "Generating colors via Matugen"
    "$MATUGEN_BIN" image "$img"
    ok "Colors generated"
}

# ============================================================
# MAIN
# ============================================================
main() {
    detect_waybar_state

    case "${1:-random}" in
        random)
            img=$(get_random_wallpaper)
            ;;
        *)
            img="$1"
            ;;
    esac

    if [[ ! -f "$img" ]]; then
        err "Wallpaper not found: $img"
        exit 1
    fi

    set_wallpaper "$img"
    generate_colors "$img"
    restore_waybar_state

    ok "Theme change complete"
}

main "$@"
