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
#   theme_ctl set --no-wall --mode light
#   theme_ctl random
#   theme_ctl refresh
#   theme_ctl get
# ==============================================================================

set -euo pipefail

# --- CONFIGURATION ---
readonly STATE_DIR="${HOME}/.config/dusky/settings/dusky_theme"
readonly STATE_FILE="${STATE_DIR}/state.conf"
readonly LOCK_FILE="/tmp/theme_ctl.lock"
readonly PUBLIC_STATE_FILE="${STATE_DIR}/state"
readonly TRACK_LIGHT="${STATE_DIR}/light_wal"
readonly TRACK_DARK="${STATE_DIR}/dark_wal"
readonly BASE_PICTURES="${HOME}/Pictures"
readonly WALLPAPER_ROOT="${BASE_PICTURES}/wallpapers"
readonly ACTIVE_THEME_DIR="${WALLPAPER_ROOT}/active_theme"
readonly DEFAULT_MODE="dark"
readonly DEFAULT_TYPE="scheme-tonal-spot"
readonly DEFAULT_CONTRAST="0"
readonly FLOCK_TIMEOUT_SEC=30
readonly DAEMON_POLL_INTERVAL=0.1
readonly DAEMON_POLL_LIMIT=50
readonly WAYBAR_STATE_FILE="${XDG_RUNTIME_DIR:-/tmp}/waybar_was_running"

# --- STATE VARIABLES (populated by read_state) ---
THEME_MODE=""
MATUGEN_TYPE=""
MATUGEN_CONTRAST=""

# --- CLEANUP TRACKING ---
_TEMP_FILE=""

cleanup() {
    local exit_code=$?
    if [[ -n "${_TEMP_FILE:-}" && -e "$_TEMP_FILE" ]]; then
        rm -f "$_TEMP_FILE"
    fi
    exit "$exit_code"
}

trap cleanup EXIT

# --- HELPER FUNCTIONS ---

log() { printf '\033[1;34m::\033[0m %s\n' "$*"; }
err() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; }
die() { err "$@"; exit 1; }

trim_trailing() {
    local str="$1"
    printf '%s' "${str%"${str##*[![:space:]]}"}"
}

check_deps() {
    local cmd
    local -a missing=()
    # Kept find/sort checks for safety despite AI critique (Trust but Verify)
    for cmd in swww matugen flock find sort; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    (( ${#missing[@]} == 0 )) || die "Missing required commands: ${missing[*]}"
}

# --- WAYBAR STATE ---

detect_waybar_state() {
    rm -f "$WAYBAR_STATE_FILE"
    pgrep -x waybar >/dev/null 2>&1 && touch "$WAYBAR_STATE_FILE" || true
}

restore_waybar_state() {
    local autostart="${HOME}/user_scripts/waybar/waybar_autostart.sh"
    if [[ -f "$WAYBAR_STATE_FILE" ]]; then
        rm -f "$WAYBAR_STATE_FILE"
        [[ -x "$autostart" ]] && { "$autostart" & } || true
    fi
}

# --- STATE MANAGEMENT ---

update_public_state() {
    local mode="$1"
    local state_val

    [[ -d "$STATE_DIR" ]] || mkdir -p "$STATE_DIR"

    if [[ "$mode" == "light" ]]; then
        state_val=1
    else
        state_val=0
    fi

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

        # Strict quote stripping: only strip if both start and end match
        if [[ ${#value} -ge 2 ]]; then
            if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
                value="${value:1:-1}"
            elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
                value="${value:1:-1}"
            fi
        fi

        case "$key" in
            THEME_MODE)       THEME_MODE="$value" ;;
            MATUGEN_TYPE)     MATUGEN_TYPE="$value" ;;
            MATUGEN_CONTRAST) MATUGEN_CONTRAST="$value" ;;
        esac
    done < "$STATE_FILE"
}

init_state() {
    [[ -d "$STATE_DIR" ]] || mkdir -p "$STATE_DIR"

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

    (( found )) || printf '%s=%s\n' "$target_key" "$new_value" >> "$_TEMP_FILE"

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
                if [[ -d "$ACTIVE_THEME_DIR" ]]; then
                    # FATAL FIX: If target storage exists, abort to prevent nesting/corruption.
                    if [[ -d "$stored_light" ]]; then
                        die "FATAL: Ambiguous State. '${stored_light}' already exists. Cannot stash active theme safely."
                    else
                        mv "$ACTIVE_THEME_DIR" "$stored_light"
                    fi
                fi
                mv "$stored_dark" "$ACTIVE_THEME_DIR"
            elif [[ ! -d "$ACTIVE_THEME_DIR" ]]; then
                log "WARN: Neither stored 'dark' nor 'active_theme' found."
            fi
        else
            if [[ -d "$stored_light" ]]; then
                if [[ -d "$ACTIVE_THEME_DIR" ]]; then
                    # FATAL FIX: If target storage exists, abort to prevent nesting/corruption.
                    if [[ -d "$stored_dark" ]]; then
                        die "FATAL: Ambiguous State. '${stored_dark}' already exists. Cannot stash active theme safely."
                    else
                        mv "$ACTIVE_THEME_DIR" "$stored_dark"
                    fi
                fi
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
    pgrep -x swaync &>/dev/null && return 0
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

select_next_wallpaper() {
    local track_file
    if [[ "$THEME_MODE" == "light" ]]; then
        track_file="$TRACK_LIGHT"
    else
        track_file="$TRACK_DARK"
    fi

    local -a wallpapers
    mapfile -d '' wallpapers < <(
        find "${ACTIVE_THEME_DIR}" -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o -iname "*.webp" -o -iname "*.gif" \) -print0 | sort -z -V
    )

    # Fallback: check parent root if active theme is empty
    if (( ${#wallpapers[@]} == 0 )); then
        mapfile -d '' wallpapers < <(
            find "${WALLPAPER_ROOT}" -maxdepth 1 -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o -iname "*.webp" -o -iname "*.gif" \) -print0 | sort -z -V
        )
    fi

    (( ${#wallpapers[@]} > 0 )) || return 1

    local last_wal=""
    [[ -f "$track_file" ]] && last_wal=$(<"$track_file")

    local next_index=0

    if [[ -n "$last_wal" ]]; then
        local i
        for i in "${!wallpapers[@]}"; do
            if [[ "${wallpapers[$i]##*/}" == "$last_wal" ]]; then
                next_index=$(( i + 1 ))
                break
            fi
        done
    fi

    (( next_index >= ${#wallpapers[@]} )) && next_index=0

    local selected="${wallpapers[$next_index]}"

    mkdir -p "${track_file%/*}"
    printf '%s' "${selected##*/}" > "$track_file"

    printf '%s' "$selected"
}

apply_random_wallpaper() {
    local wallpaper
    wallpaper=$(select_next_wallpaper) || die "No wallpapers found in ${ACTIVE_THEME_DIR}"

    log "Selected: ${wallpaper##*/}"

    detect_waybar_state

    ensure_swww_running
    swww img "$wallpaper" \
        --transition-type grow \
        --transition-duration 2 \
        --transition-fps 60

    generate_colors "$wallpaper"

    restore_waybar_state
}

regenerate_current() {
    local swww_output current_wallpaper resolved_wallpaper filename
    ensure_swww_running

    swww_output=$(swww query 2>&1 | head -n 1) || die "swww query failed: $swww_output"
    [[ -n "$swww_output" ]] || die "swww returned empty output"

    current_wallpaper="${swww_output##*image: }"
    current_wallpaper=$(trim_trailing "$current_wallpaper")

    resolved_wallpaper="$current_wallpaper"

    # Resolve wallpaper if directory swap moved the file
    if [[ ! -f "$resolved_wallpaper" ]]; then
        filename="${current_wallpaper##*/}"

        if [[ -f "${BASE_PICTURES}/dark/${filename}" ]]; then
            resolved_wallpaper="${BASE_PICTURES}/dark/${filename}"
        elif [[ -f "${BASE_PICTURES}/light/${filename}" ]]; then
            resolved_wallpaper="${BASE_PICTURES}/light/${filename}"
        fi
    fi

    [[ -f "$resolved_wallpaper" ]] || die "Image file does not exist: ${current_wallpaper}"

    if [[ "$resolved_wallpaper" != "$current_wallpaper" ]]; then
        log "Wallpaper moved; resolved to: ${resolved_wallpaper}"
    else
        log "Current wallpaper: ${resolved_wallpaper##*/}"
    fi

    detect_waybar_state
    generate_colors "$resolved_wallpaper"
    restore_waybar_state
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

    command -v gsettings &>/dev/null && \
        gsettings set org.gnome.desktop.interface color-scheme "prefer-${THEME_MODE}" 2>/dev/null || true
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
              --no-wall   Prevent wallpaper change (e.g., during refresh)
  random    Cycle to next wallpaper (chronological/natural sort) and apply theme.
  refresh   Regenerate colors for current wallpaper.
  get       Show current configuration.

Examples:
  theme_ctl set --mode dark --type scheme-vibrant
  theme_ctl set --no-wall --mode light
  theme_ctl random
  theme_ctl refresh
EOF
}

cmd_get() {
    if [[ -f "$STATE_FILE" ]]; then
        cat "$STATE_FILE"
    else
        printf '# State file not found\n'
    fi
    printf '\n# Public State (%s):\n' "$PUBLIC_STATE_FILE"
    if [[ -f "$PUBLIC_STATE_FILE" ]]; then
        cat "$PUBLIC_STATE_FILE"
    else
        printf 'N/A\n'
    fi
}

cmd_set() {
    local do_refresh=0 mode_changed=0 same_mode_requested=0 skip_wall=0

    while (( $# > 0 )); do
        case "$1" in
            --mode)
                [[ -n "${2:-}" ]] || die "--mode requires a value"
                [[ "$2" == "light" || "$2" == "dark" ]] || die "--mode must be 'light' or 'dark'"

                if [[ "$THEME_MODE" != "$2" ]]; then
                    update_state_key "THEME_MODE" "$2"
                    mode_changed=1
                else
                    same_mode_requested=1
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
            --no-wall)
                skip_wall=1
                shift
                ;;
            --help) usage; exit 0 ;;
            *) die "Unknown option: $1" ;;
        esac
    done

    read_state

    # 1. Action: Wallpaper Shuffle & Swap (Standard use)
    if (( ! skip_wall )) && (( mode_changed || same_mode_requested )); then
        move_directories "$THEME_MODE"
        apply_random_wallpaper
    else
        # 2. Action: Directory Swap Only (User changed mode but suppressed wallpaper change)
        (( mode_changed )) && move_directories "$THEME_MODE"

        # 3. Action: Color Refresh (Settings changed or user forced same-mode refresh)
        if (( do_refresh || same_mode_requested || mode_changed )); then
            regenerate_current
        fi
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
