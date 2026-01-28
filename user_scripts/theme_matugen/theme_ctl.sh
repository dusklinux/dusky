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
#   1. INTERNAL STATE: Reads/Writes to ~/.config/dusky/settings/dusky_theme/state.conf
#   2. PUBLIC STATE:   Writes 0/1 state to ~/.config/dusky/settings/dusky_theme/state
#   3. LOCKING:        Uses file locking (flock) to prevent directory move race conditions.
#   4. DIRECTORY OPS:  Swaps stored folders into 'active_theme' directory.
#
# Usage:
#   theme_ctl set --mode dark --type scheme-vibrant
#   theme_ctl random
#   theme_ctl refresh
#   theme_ctl get
# ==============================================================================

set -euo pipefail

# --- CONFIGURATION ---
# Updated: State now lives in the Dusky settings directory
readonly STATE_DIR="${HOME}/.config/dusky/settings/dusky_theme"
readonly STATE_FILE="${STATE_DIR}/state.conf"
readonly LOCK_FILE="/tmp/theme_ctl.lock"

# Public state for external scripts (0=Dark, 1=Light)
# Now resides in the same directory as STATE_FILE
readonly PUBLIC_STATE_FILE="${STATE_DIR}/state"

readonly BASE_PICTURES="${HOME}/Pictures"
readonly WALLPAPER_ROOT="${BASE_PICTURES}/wallpapers"
readonly ACTIVE_THEME_DIR="${WALLPAPER_ROOT}/active_theme"

readonly DEFAULT_MODE="dark"
readonly DEFAULT_TYPE="scheme-tonal-spot"
readonly DEFAULT_CONTRAST="0"

# Timeouts & Limits
readonly FLOCK_TIMEOUT_SEC=30
readonly DAEMON_POLL_INTERVAL=0.1
readonly DAEMON_POLL_LIMIT=50   # 50 * 0.1s = 5 seconds max

# --- STATE VARIABLES (populated by read_state) ---
THEME_MODE=""
MATUGEN_TYPE=""
MATUGEN_CONTRAST=""

# --- CLEANUP TRACKING ---
_TEMP_FILE=""

cleanup() {
    # Safer cleanup check: only delete if variable is set and file exists
    if [[ -n "${_TEMP_FILE:-}" && -e "$_TEMP_FILE" ]]; then
        rm -f "$_TEMP_FILE"
    fi
}
trap cleanup EXIT

# --- HELPER FUNCTIONS ---

log() { printf '\033[1;34m::\033[0m %s\n' "$*"; }
err() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; }
die() { err "$@"; exit 1; }

# Helper to trim trailing whitespace
trim_trailing() {
    local str="$1"
    printf '%s' "${str%"${str##*[![:space:]]}"}"
}

check_deps() {
    local cmd
    local -a missing=()
    for cmd in swww matugen flock; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    (( ${#missing[@]} == 0 )) || die "Missing required commands: ${missing[*]}"
}

# --- STATE MANAGEMENT ---

update_public_state() {
    local mode="$1"
    local state_val

    # Ensure directory exists (uses shared STATE_DIR now)
    [[ -d "$STATE_DIR" ]] || mkdir -p "$STATE_DIR"

    if [[ "$mode" == "light" ]]; then
        state_val=1
    else
        state_val=0
    fi

    # Atomic write
    printf '%s\n' "$state_val" > "${PUBLIC_STATE_FILE}.tmp"
    mv -f "${PUBLIC_STATE_FILE}.tmp" "$PUBLIC_STATE_FILE"
}

read_state() {
    THEME_MODE="$DEFAULT_MODE"
    MATUGEN_TYPE="$DEFAULT_TYPE"
    MATUGEN_CONTRAST="$DEFAULT_CONTRAST"

    [[ -f "$STATE_FILE" ]] || return 0

    local key value
    while IFS='=' read -r key value || [[ -n "$key" ]]; do
        [[ -z "$key" || "${key:0:1}" == "#" ]] && continue
        
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
    [[ -d "$STATE_DIR" ]] || mkdir -p "$STATE_DIR"

    # Check if file is missing OR empty (-s returns true if file exists and size > 0)
    if [[ ! -s "$STATE_FILE" ]]; then
        log "Initializing new state file at ${STATE_FILE}..."
        printf '%s\n' \
            "# Dusky Theme State File" \
            "THEME_MODE=${DEFAULT_MODE}" \
            "MATUGEN_TYPE=${DEFAULT_TYPE}" \
            "MATUGEN_CONTRAST=${DEFAULT_CONTRAST}" \
            > "$STATE_FILE"
    fi

    read_state
    update_public_state "$THEME_MODE"
}

update_state_key() {
    local target_key="$1" new_value="$2"
    local found=0 line

    _TEMP_FILE=$(mktemp "${STATE_DIR}/state.conf.XXXXXX")

    # If STATE_FILE is missing/empty, loop won't run, so we just append later
    if [[ -s "$STATE_FILE" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            if [[ "$line" == "${target_key}="* ]]; then
                printf '%s=%s\n' "$target_key" "$new_value"
                found=1
            else
                printf '%s\n' "$line"
            fi
        done < "$STATE_FILE" > "$_TEMP_FILE"
    fi

    # If key wasn't found (or file was empty), append it
    if (( ! found )); then
        printf '%s=%s\n' "$target_key" "$new_value" >> "$_TEMP_FILE"
    fi

    mv -f "$_TEMP_FILE" "$STATE_FILE"
    _TEMP_FILE=""
    
    if [[ "$target_key" == "THEME_MODE" ]]; then
        update_public_state "$new_value"
    fi
}

# --- DIRECTORY MANAGER ---

move_directories() {
    local target_mode="$1"
    local stored_light="${BASE_PICTURES}/light"
    local stored_dark="${BASE_PICTURES}/dark"

    log "Reconciling directories for mode: ${target_mode}"

    (
        flock -w "$FLOCK_TIMEOUT_SEC" -x 200 || die "Could not acquire directory lock"

        if [[ "$target_mode" == "dark" ]]; then
            if [[ -d "$stored_dark" ]]; then
                [[ -d "$ACTIVE_THEME_DIR" ]] && mv "$ACTIVE_THEME_DIR" "$stored_light"
                mv "$stored_dark" "$ACTIVE_THEME_DIR"
            elif [[ ! -d "$ACTIVE_THEME_DIR" ]]; then
                log "WARN: Neither stored 'dark' nor 'active_theme' found."
            fi
        else
            if [[ -d "$stored_light" ]]; then
                [[ -d "$ACTIVE_THEME_DIR" ]] && mv "$ACTIVE_THEME_DIR" "$stored_dark"
                mv "$stored_light" "$ACTIVE_THEME_DIR"
            elif [[ ! -d "$ACTIVE_THEME_DIR" ]]; then
                log "WARN: Neither stored 'light' nor 'active_theme' found."
            fi
        fi
    ) 200>"$LOCK_FILE"
}

# --- WALLPAPER & MATUGEN LOGIC ---

wait_for_process() {
    local proc_name="$1"
    local attempts=0
    while ! pgrep -x "$proc_name" &>/dev/null; do
        (( ++attempts > DAEMON_POLL_LIMIT )) && return 1
        sleep "$DAEMON_POLL_INTERVAL"
    done
    return 0
}

ensure_swww_running() {
    pgrep -x swww-daemon &>/dev/null && return 0
    log "Starting swww-daemon..."
    
    if systemctl --user cat swww.service &>/dev/null; then
        systemctl --user start swww.service
        sleep 0.3
        pgrep -x swww-daemon &>/dev/null && return 0
    fi

    if command -v uwsm-app &>/dev/null; then
        uwsm-app -- swww-daemon --format xrgb &
        disown
    else
        swww-daemon --format xrgb &
        disown
    fi

    wait_for_process "swww-daemon" || die "swww-daemon failed to start"
}

ensure_swaync_running() {
    pgrep -x swaync >/dev/null && return 0
    log "Starting swaync..."

    if command -v uwsm-app &>/dev/null; then
        uwsm-app -- swaync &
        disown
    else
        swaync &
        disown
    fi

    if ! wait_for_process "swaync"; then
        log "WARN: swaync failed to start (Matugen hooks might fail)"
        return 0
    fi
    sleep 0.5
}

select_random_wallpaper() {
    (
        shopt -s nullglob globstar
        # Try active theme directory first (recursive)
        local -a wallpapers=("${ACTIVE_THEME_DIR}"/**/*.{jpg,jpeg,png,webp,gif})

        # FALLBACK: If active theme is empty, check the parent root (flat search)
        if (( ${#wallpapers[@]} == 0 )); then
            wallpapers=("${WALLPAPER_ROOT}"/*.{jpg,jpeg,png,webp,gif})
        fi

        (( ${#wallpapers[@]} > 0 )) || exit 1
        printf '%s' "${wallpapers[RANDOM % ${#wallpapers[@]}]}"
    )
}


apply_random_wallpaper() {
    local wallpaper
    wallpaper=$(select_random_wallpaper) || die "No wallpapers found in ${ACTIVE_THEME_DIR}"

    log "Selected: ${wallpaper##*/}"

    ensure_swww_running
    swww img "$wallpaper" \
        --transition-type grow \
        --transition-duration 2 \
        --transition-fps 60

    generate_colors "$wallpaper"
}

regenerate_current() {
    local swww_output current_wallpaper
    ensure_swww_running

    swww_output=$(swww query 2>&1 | head -n 1) || die "swww query failed: $swww_output"
    [[ -n "$swww_output" ]] || die "swww returned empty output"

    current_wallpaper="${swww_output##*image: }"
    current_wallpaper=$(trim_trailing "$current_wallpaper")

    [[ -f "$current_wallpaper" ]] || die "Image file does not exist: ${current_wallpaper}"

    log "Current wallpaper: ${current_wallpaper##*/}"
    generate_colors "$current_wallpaper"
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
            log "Note: gsettings update skipped"
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
  get       Show current configuration.

Examples:
  theme_ctl set --mode dark --type scheme-vibrant
  theme_ctl random
  theme_ctl refresh
EOF
}

cmd_get() {
    cat "$STATE_FILE"
    printf '\n'
    printf '# Public State (%s):\n' "$PUBLIC_STATE_FILE"
    if [[ -f "$PUBLIC_STATE_FILE" ]]; then
        cat "$PUBLIC_STATE_FILE"
    else
        printf 'N/A\n'
    fi
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
            --help) usage; exit 0 ;;
            *) die "Unknown option: $1" ;;
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
        cmd_get
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
