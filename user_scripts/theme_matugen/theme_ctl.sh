#!/usr/bin/env bash
# ==============================================================================
# THEME CONTROLLER (theme_ctl)
# ==============================================================================
# Description: Centralized state manager for System Theming.
#              Handles Matugen config, Physical Directory Swaps, and Wallpaper updates.
#
# Ecosystem:   Arch Linux / Hyprland / UWSM
#
# Architecture:
#   1. STATE:    Reads/Writes to ~/.config/matugen/state.conf (Safe Parser)
#   2. LOCKING:  Uses file locking (flock) to prevent directory move race conditions.
#   3. PARSING:  Robustly handles swww output and matugen arguments.
#
# Usage:
#   theme_ctl set --mode dark --type scheme-vibrant
#   theme_ctl random
#   theme_ctl refresh
#   theme_ctl get
# ==============================================================================

set -euo pipefail

# --- CONFIGURATION ---
# Updated to use the existing matugen config directory
readonly STATE_DIR="${HOME}/.config/matugen"
readonly STATE_FILE="${STATE_DIR}/state.conf"
readonly LOCK_FILE="/tmp/theme_ctl.lock"

readonly BASE_PICTURES="${HOME}/Pictures"
readonly WALLPAPER_ROOT="${BASE_PICTURES}/wallpapers"

readonly DEFAULT_MODE="dark"
readonly DEFAULT_TYPE="scheme-tonal-spot"
readonly DEFAULT_CONTRAST="0"

# Timeouts & Limits
readonly FLOCK_TIMEOUT_SEC=30
readonly DAEMON_POLL_LIMIT=50   # 50 iterations * 0.1s = 5 seconds max

# --- STATE VARIABLES (populated by read_state) ---
THEME_MODE=""
MATUGEN_TYPE=""
MATUGEN_CONTRAST=""

# --- CLEANUP TRACKING ---
_TEMP_FILE=""

cleanup() {
    [[ -n "$_TEMP_FILE" && -e "$_TEMP_FILE" ]] && rm -f "$_TEMP_FILE" || true
}
trap cleanup EXIT

# --- HELPER FUNCTIONS ---

log() { printf '\033[1;34m::\033[0m %s\n' "$*"; }
err() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; }
die() { err "$@"; exit 1; }

check_deps() {
    local cmd missing=()
    for cmd in swww matugen flock; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    (( ${#missing[@]} == 0 )) || die "Missing required commands: ${missing[*]}"
}

# --- STATE MANAGEMENT ---
# Safe key=value reader. Does NOT source the file (avoids code injection).

read_state() {
    THEME_MODE="$DEFAULT_MODE"
    MATUGEN_TYPE="$DEFAULT_TYPE"
    MATUGEN_CONTRAST="$DEFAULT_CONTRAST"

    [[ -f "$STATE_FILE" ]] || return 0

    local key value
    while IFS='=' read -r key value || [[ -n "$key" ]]; do
        # Skip comments and empty lines
        [[ -z "$key" || "${key:0:1}" == "#" ]] && continue

        # Strip one layer of surrounding quotes
        value="${value#[\"\']}"
        value="${value%[\"\']}"

        case "$key" in
            THEME_MODE)       THEME_MODE="$value" ;;
            MATUGEN_TYPE)     MATUGEN_TYPE="$value" ;;
            MATUGEN_CONTRAST) MATUGEN_CONTRAST="$value" ;;
        esac
    done < "$STATE_FILE"
}

init_state() {
    # Ensure directory exists (it should, but safety first)
    [[ -d "$STATE_DIR" ]] || mkdir -p "$STATE_DIR"

    if [[ ! -f "$STATE_FILE" ]]; then
        log "Initializing new state file at ${STATE_FILE}..."
        printf '%s\n' \
            "# Dusky Theme State File" \
            "THEME_MODE=${DEFAULT_MODE}" \
            "MATUGEN_TYPE=${DEFAULT_TYPE}" \
            "MATUGEN_CONTRAST=${DEFAULT_CONTRAST}" \
            > "$STATE_FILE"
    fi

    read_state
}

update_state_key() {
    local target_key="$1" new_value="$2"
    local found=0 line

    # Create temp file safely
    _TEMP_FILE=$(mktemp "${STATE_DIR}/state.conf.XXXXXX")

    # Pure Bash read/write loop.
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" == "${target_key}="* ]]; then
            printf '%s=%s\n' "$target_key" "$new_value"
            found=1
        else
            printf '%s\n' "$line"
        fi
    done < "$STATE_FILE" > "$_TEMP_FILE"

    # Append key if it was missing
    (( found )) || printf '%s=%s\n' "$target_key" "$new_value" >> "$_TEMP_FILE"

    # Atomic Move
    mv -f "$_TEMP_FILE" "$STATE_FILE"
    _TEMP_FILE=""
}

# --- DIRECTORY MANAGER ---

move_directories() {
    local target_mode="$1"

    local active_light="${WALLPAPER_ROOT}/light"
    local active_dark="${WALLPAPER_ROOT}/dark"
    local stored_light="${BASE_PICTURES}/light"
    local stored_dark="${BASE_PICTURES}/dark"

    log "Reconciling directories for mode: ${target_mode}"

    # Use flock with timeout (-w) to prevent infinite hangs
    (
        flock -w "$FLOCK_TIMEOUT_SEC" -x 200 || die "Could not acquire directory lock within ${FLOCK_TIMEOUT_SEC}s"

        if [[ "$target_mode" == "dark" ]]; then
            # 1. Hide Light (if it exists)
            [[ -d "$active_light" ]] && mv "$active_light" "$BASE_PICTURES/"

            # 2. Deploy Dark (if it exists in storage)
            [[ -d "$stored_dark" ]]  && mv "$stored_dark" "$WALLPAPER_ROOT/"

            # 3. Soft Check: Warn but do NOT die if missing
            [[ -d "$active_dark" ]] || log "Note: No dedicated 'dark' folder found. Using generic wallpapers."
        else
            # 1. Hide Dark (if it exists)
            [[ -d "$active_dark" ]]  && mv "$active_dark" "$BASE_PICTURES/"

            # 2. Deploy Light (if it exists in storage)
            [[ -d "$stored_light" ]] && mv "$stored_light" "$WALLPAPER_ROOT/"

            # 3. Soft Check: Warn but do NOT die if missing
            [[ -d "$active_light" ]] || log "Note: No dedicated 'light' folder found. Using generic wallpapers."
        fi
    ) 200>"$LOCK_FILE"
}

# --- WALLPAPER & MATUGEN LOGIC ---

ensure_swww_running() {
    pgrep -x swww-daemon &>/dev/null && return 0

    log "Starting swww-daemon..."

    # 1. Prefer systemd user service
    if systemctl --user list-unit-files swww.service &>/dev/null; then
        systemctl --user start swww.service
        sleep 0.3
        pgrep -x swww-daemon &>/dev/null && return 0
    fi

    # 2. Fallback: launch via uwsm-app
    if command -v uwsm-app &>/dev/null; then
        uwsm-app -- swww-daemon --format xrgb &
        disown
    else
        # 3. Last resort: Raw background process
        swww-daemon --format xrgb &
        disown
    fi

    # Wait for daemon to appear
    local attempts=0
    while ! pgrep -x swww-daemon &>/dev/null; do
        (( ++attempts > DAEMON_POLL_LIMIT )) && die "swww-daemon failed to start within 5s"
        sleep 0.1
    done
}

ensure_swaync_running() {
    pgrep -x swaync >/dev/null && return 0

    log "Starting swaync (required for matugen hooks)..."

    if command -v uwsm-app &>/dev/null; then
        uwsm-app -- swaync &
        disown
    else
        swaync &
        disown
    fi

    local attempt=0
    while ! pgrep -x swaync >/dev/null; do
        sleep 0.2
        ((++attempt))

        if (( attempt > 25 )); then
            log "WARN: swaync failed to start. Matugen may hang."
            break
        fi
    done

    sleep 0.5
}

apply_random_wallpaper() {
    local target_wallpaper

    # Run globbing in a subshell
    target_wallpaper=$(
        shopt -s nullglob globstar
        local -a wallpapers=("${WALLPAPER_ROOT}"/**/*.{jpg,jpeg,png,webp,gif})

        (( ${#wallpapers[@]} > 0 )) || exit 1

        printf '%s' "${wallpapers[RANDOM % ${#wallpapers[@]}]}"
    ) || die "No wallpapers found in ${WALLPAPER_ROOT}"

    log "Selected: ${target_wallpaper##*/}"

    ensure_swww_running
    swww img "$target_wallpaper" \
        --transition-type grow \
        --transition-duration 2 \
        --transition-fps 60

    generate_colors "$target_wallpaper"
}

regenerate_current() {
    local swww_line current_img

    ensure_swww_running

    # Use 'head -n 1' to grab the first monitor only
    swww_line=$(swww query 2>/dev/null | head -n 1) || die "swww query failed"
    [[ -n "$swww_line" ]] || die "swww returned empty output"

    # Extract path after "image: "
    current_img="${swww_line##*image: }"

    # Trim trailing whitespace
    current_img="${current_img%"${current_img##*[![:space:]]}"}"

    [[ -f "$current_img" ]] || die "Image file does not exist: ${current_img}"

    log "Current wallpaper: ${current_img##*/}"
    generate_colors "$current_img"
}

generate_colors() {
    local img="$1"

    ensure_swaync_running

    read_state

    log "Matugen: Mode=[${THEME_MODE}] Type=[${MATUGEN_TYPE}] Contrast=[${MATUGEN_CONTRAST}]"

    local -a cmd=(matugen --mode "$THEME_MODE")

    [[ "$MATUGEN_TYPE" != "disable" ]]     && cmd+=(--type "$MATUGEN_TYPE")
    [[ "$MATUGEN_CONTRAST" != "disable" ]] && cmd+=(--contrast "$MATUGEN_CONTRAST")

    cmd+=(image "$img")

    "${cmd[@]}" || die "Matugen generation failed"

    if command -v gsettings &>/dev/null; then
        if ! gsettings set org.gnome.desktop.interface color-scheme "prefer-${THEME_MODE}" 2>/dev/null; then
            log "Note: gsettings update skipped (DBus/schema issue)"
        fi
    fi
}

# --- CLI HANDLER ---

usage() {
    cat <<'EOF'
Usage: theme_ctl [COMMAND] [OPTIONS]

Commands:
  set       Update settings and apply changes.
              --mode <light|dark>
              --type <scheme-*|disable>
              --contrast <num|disable>
              --defaults  Reset all settings to defaults
  random    Pick random wallpaper and apply theme.
  refresh   Regenerate colors for current wallpaper.
            (alias: apply)
  get       Show current configuration.

Examples:
  theme_ctl set --mode dark --type scheme-vibrant
  theme_ctl random
  theme_ctl refresh
EOF
}

cmd_set() {
    local do_refresh=0 mode_changed=0 force_random=0

    while (( $# > 0 )); do
        case "$1" in
            --mode)
                [[ -n "${2:-}" ]] || die "--mode requires a value"
                [[ "$2" == "light" || "$2" == "dark" ]] || die "--mode must be 'light' or 'dark'"

                if [[ "$THEME_MODE" != "$2" ]]; then
                    update_state_key "THEME_MODE" "$2"
                    mode_changed=1
                else
                    force_random=1
                fi
                shift 2
                ;;
            --type)
                [[ -n "${2:-}" ]] || die "--type requires a value"
                update_state_key "MATUGEN_TYPE" "$2"
                do_refresh=1
                shift 2
                ;;
            --contrast)
                [[ -n "${2:-}" ]] || die "--contrast requires a value"
                update_state_key "MATUGEN_CONTRAST" "$2"
                do_refresh=1
                shift 2
                ;;
            --defaults)
                update_state_key "THEME_MODE" "$DEFAULT_MODE"
                update_state_key "MATUGEN_TYPE" "$DEFAULT_TYPE"
                update_state_key "MATUGEN_CONTRAST" "$DEFAULT_CONTRAST"
                mode_changed=1
                shift
                ;;
            --help)
                usage; exit 0 ;;
            *)
                die "Unknown option: $1" ;;
        esac
    done

    read_state

    if (( mode_changed || force_random )); then
        move_directories "$THEME_MODE"
        apply_random_wallpaper
    elif (( do_refresh )); then
        regenerate_current
    fi
}

# --- MAIN ---

check_deps
init_state

case "${1:-}" in
    set)
        shift
        cmd_set "$@"
        ;;
    random)
        move_directories "$THEME_MODE"
        apply_random_wallpaper
        ;;
    refresh|apply)
        move_directories "$THEME_MODE"
        regenerate_current
        ;;
    get)
        cat "$STATE_FILE"
        ;;
    -h|--help|help)
        usage
        ;;
    "")
        usage
        exit 1
        ;;
    *)
        die "Unknown command: $1"
        ;;
esac
