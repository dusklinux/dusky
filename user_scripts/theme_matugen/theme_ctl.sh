#!/usr/bin/env bash
# ==============================================================================
# THEME CONTROLLER (theme_ctl)
# ==============================================================================
# Description: Centralized state manager for system theming.
#              Handles Matugen config, physical directory swaps, and wallpaper updates.
#
# Ecosystem:   Arch Linux / Hyprland / UWSM / Wayland
#
# Architecture:
#   1. INTERNAL STATE: ~/.config/dusky/settings/dusky_theme/state.conf
#   2. PUBLIC STATE:   ~/.config/dusky/settings/dusky_theme/state (true/false)
#   3. LOCKING:        Single global flock across all mutating operations via run_locked
#   4. DIRECTORY OPS:  Swaps stored folders into wallpaper_root/active_theme
#
# Usage:
#   theme_ctl set --mode dark --type scheme-vibrant
#   theme_ctl set --index 1 --base16 wal
#   theme_ctl set --no-wall --mode light
#   theme_ctl next
#   theme_ctl prev
#   theme_ctl random
#   theme_ctl refresh
#   theme_ctl color FF0000
#   theme_ctl get
# ==============================================================================

set -euo pipefail

# --- CONFIGURATION ---
readonly STATE_DIR="${HOME}/.config/dusky/settings/dusky_theme"
readonly STATE_FILE="${STATE_DIR}/state.conf"
readonly PUBLIC_STATE_FILE="${STATE_DIR}/state"
readonly TRACK_LIGHT="${STATE_DIR}/light_wal"
readonly TRACK_DARK="${STATE_DIR}/dark_wal"

readonly BASE_PICTURES="${HOME}/Pictures"
readonly STORED_LIGHT_DIR="${BASE_PICTURES}/light"
readonly STORED_DARK_DIR="${BASE_PICTURES}/dark"
readonly WALLPAPER_ROOT="${BASE_PICTURES}/wallpapers"
readonly ACTIVE_THEME_DIR="${WALLPAPER_ROOT}/active_theme"

readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/theme_ctl.lock"
readonly FLOCK_TIMEOUT_SEC=30

readonly DEFAULT_MODE="dark"
readonly DEFAULT_TYPE="scheme-tonal-spot"
readonly DEFAULT_CONTRAST="0"
readonly DEFAULT_COLOR_INDEX="0"
readonly DEFAULT_BASE16="disable"

readonly DAEMON_POLL_INTERVAL=0.1
readonly DAEMON_POLL_LIMIT=50

# --- STATE VARIABLES ---
THEME_MODE=""
MATUGEN_TYPE=""
MATUGEN_CONTRAST=""
SOURCE_COLOR_INDEX=""
BASE16_BACKEND=""
STATE_NEEDS_REWRITE=0

# --- CLEANUP TRACKING ---
_TEMP_FILE=""

cleanup() {
    local exit_code=$?
    if [[ -n "${_TEMP_FILE:-}" && -e "$_TEMP_FILE" ]]; then
        rm -f -- "$_TEMP_FILE"
    fi
    trap - EXIT
    exit "$exit_code"
}

trap cleanup EXIT

# --- HELPERS ---

log()  { printf '\033[1;34m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

trim_trailing() {
    local str="$1"
    printf '%s' "${str%"${str##*[![:space:]]}"}"
}

ensure_dir() {
    local dir="$1"
    if [[ -e "$dir" && ! -d "$dir" ]]; then
        die "Path exists but is not a directory: $dir"
    fi
    [[ -d "$dir" ]] || mkdir -p -- "$dir"
}

process_running() {
    local proc_name="$1"
    pgrep -xu "$UID" "$proc_name" >/dev/null 2>&1
}

check_deps() {
    local cmd
    local -a missing=()

    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done

    (( ${#missing[@]} == 0 )) || die "Missing required commands: ${missing[*]}"
}

is_valid_matugen_type() {
    case "$1" in
        disable|scheme-content|scheme-expressive|scheme-fidelity|scheme-fruit-salad|scheme-monochrome|scheme-neutral|scheme-rainbow|scheme-tonal-spot|scheme-vibrant)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

is_valid_contrast() {
    local value="$1"

    [[ "$value" == "disable" ]] && return 0
    [[ "$value" =~ ^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]] || return 1

    LC_ALL=C awk -v v="$value" 'BEGIN { exit !(v >= -1 && v <= 1) }'
}

is_valid_base16_backend() {
    [[ "$1" == "disable" || "$1" == "wal" ]]
}

tracker_file_for_mode() {
    local mode="$1"
    if [[ "$mode" == "light" ]]; then
        printf '%s\n' "$TRACK_LIGHT"
    else
        printf '%s\n' "$TRACK_DARK"
    fi
}

# --- STATE MANAGEMENT ---

write_public_state() {
    local mode="$1"
    local state_val

    ensure_dir "$STATE_DIR"

    if [[ "$mode" == "dark" ]]; then
        state_val="true"
    else
        state_val="false"
    fi

    _TEMP_FILE=$(mktemp "${STATE_DIR}/state.XXXXXX")
    printf '%s\n' "$state_val" > "$_TEMP_FILE"
    mv -fT -- "$_TEMP_FILE" "$PUBLIC_STATE_FILE"
    _TEMP_FILE=""
}

read_state() {
    THEME_MODE="$DEFAULT_MODE"
    MATUGEN_TYPE="$DEFAULT_TYPE"
    MATUGEN_CONTRAST="$DEFAULT_CONTRAST"
    SOURCE_COLOR_INDEX="$DEFAULT_COLOR_INDEX"
    BASE16_BACKEND="$DEFAULT_BASE16"
    STATE_NEEDS_REWRITE=0

    local -i saw_mode=0
    local -i saw_type=0
    local -i saw_contrast=0
    local -i saw_index=0
    local -i saw_base16=0
    local key value

    [[ -f "$STATE_FILE" ]] || {
        STATE_NEEDS_REWRITE=1
        return 0
    }

    while IFS='=' read -r key value || [[ -n "$key" ]]; do
        [[ -z "$key" || "${key:0:1}" == "#" ]] && continue

        if [[ ${#value} -ge 2 ]]; then
            if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
                value="${value:1:-1}"
            elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
                value="${value:1:-1}"
            fi
        fi

        case "$key" in
            THEME_MODE)
                THEME_MODE="$value"
                saw_mode=1
                ;;
            MATUGEN_TYPE)
                MATUGEN_TYPE="$value"
                saw_type=1
                ;;
            MATUGEN_CONTRAST)
                MATUGEN_CONTRAST="$value"
                saw_contrast=1
                ;;
            SOURCE_COLOR_INDEX)
                SOURCE_COLOR_INDEX="$value"
                saw_index=1
                ;;
            BASE16_BACKEND)
                BASE16_BACKEND="$value"
                saw_base16=1
                ;;
        esac
    done < "$STATE_FILE"

    case "$THEME_MODE" in
        light|dark) ;;
        *)
            warn "Invalid THEME_MODE in state file. Resetting to ${DEFAULT_MODE}."
            THEME_MODE="$DEFAULT_MODE"
            STATE_NEEDS_REWRITE=1
            ;;
    esac

    if ! is_valid_matugen_type "$MATUGEN_TYPE"; then
        warn "Invalid MATUGEN_TYPE in state file. Resetting to ${DEFAULT_TYPE}."
        MATUGEN_TYPE="$DEFAULT_TYPE"
        STATE_NEEDS_REWRITE=1
    fi

    if ! is_valid_contrast "$MATUGEN_CONTRAST"; then
        warn "Invalid MATUGEN_CONTRAST in state file. Resetting to ${DEFAULT_CONTRAST}."
        MATUGEN_CONTRAST="$DEFAULT_CONTRAST"
        STATE_NEEDS_REWRITE=1
    fi

    if ! [[ "$SOURCE_COLOR_INDEX" =~ ^[0-9]+$ ]]; then
        warn "Invalid SOURCE_COLOR_INDEX in state file. Resetting to ${DEFAULT_COLOR_INDEX}."
        SOURCE_COLOR_INDEX="$DEFAULT_COLOR_INDEX"
        STATE_NEEDS_REWRITE=1
    fi

    if ! is_valid_base16_backend "$BASE16_BACKEND"; then
        warn "Invalid BASE16_BACKEND in state file. Resetting to ${DEFAULT_BASE16}."
        BASE16_BACKEND="$DEFAULT_BASE16"
        STATE_NEEDS_REWRITE=1
    fi

    (( saw_mode )) || STATE_NEEDS_REWRITE=1
    (( saw_type )) || STATE_NEEDS_REWRITE=1
    (( saw_contrast )) || STATE_NEEDS_REWRITE=1
    (( saw_index )) || STATE_NEEDS_REWRITE=1
    (( saw_base16 )) || STATE_NEEDS_REWRITE=1
}

write_state() {
    local mode="$1"
    local type="$2"
    local contrast="$3"
    local index="$4"
    local base16="$5"

    local -i wrote_mode=0
    local -i wrote_type=0
    local -i wrote_contrast=0
    local -i wrote_index=0
    local -i wrote_base16=0
    local -i had_content=0
    local line

    ensure_dir "$STATE_DIR"

    _TEMP_FILE=$(mktemp "${STATE_DIR}/state.conf.XXXXXX")

    if [[ -s "$STATE_FILE" ]]; then
        had_content=1

        while IFS= read -r line || [[ -n "$line" ]]; do
            case "$line" in
                THEME_MODE=*)
                    if (( ! wrote_mode )); then
                        printf 'THEME_MODE=%s\n' "$mode"
                        wrote_mode=1
                    fi
                    ;;
                MATUGEN_TYPE=*)
                    if (( ! wrote_type )); then
                        printf 'MATUGEN_TYPE=%s\n' "$type"
                        wrote_type=1
                    fi
                    ;;
                MATUGEN_CONTRAST=*)
                    if (( ! wrote_contrast )); then
                        printf 'MATUGEN_CONTRAST=%s\n' "$contrast"
                        wrote_contrast=1
                    fi
                    ;;
                SOURCE_COLOR_INDEX=*)
                    if (( ! wrote_index )); then
                        printf 'SOURCE_COLOR_INDEX=%s\n' "$index"
                        wrote_index=1
                    fi
                    ;;
                BASE16_BACKEND=*)
                    if (( ! wrote_base16 )); then
                        printf 'BASE16_BACKEND=%s\n' "$base16"
                        wrote_base16=1
                    fi
                    ;;
                *)
                    printf '%s\n' "$line"
                    ;;
            esac
        done < "$STATE_FILE" > "$_TEMP_FILE"
    fi

    if (( ! had_content )); then
        printf '%s\n' "# Dusky Theme State File" > "$_TEMP_FILE"
    fi

    (( wrote_mode )) || printf 'THEME_MODE=%s\n' "$mode" >> "$_TEMP_FILE"
    (( wrote_type )) || printf 'MATUGEN_TYPE=%s\n' "$type" >> "$_TEMP_FILE"
    (( wrote_contrast )) || printf 'MATUGEN_CONTRAST=%s\n' "$contrast" >> "$_TEMP_FILE"
    (( wrote_index )) || printf 'SOURCE_COLOR_INDEX=%s\n' "$index" >> "$_TEMP_FILE"
    (( wrote_base16 )) || printf 'BASE16_BACKEND=%s\n' "$base16" >> "$_TEMP_FILE"

    mv -fT -- "$_TEMP_FILE" "$STATE_FILE"
    _TEMP_FILE=""

    write_public_state "$mode"

    THEME_MODE="$mode"
    MATUGEN_TYPE="$type"
    MATUGEN_CONTRAST="$contrast"
    SOURCE_COLOR_INDEX="$index"
    BASE16_BACKEND="$base16"
    STATE_NEEDS_REWRITE=0
}

init_state() {
    ensure_dir "$STATE_DIR"
    read_state

    if [[ ! -s "$STATE_FILE" ]]; then
        log "Initializing new state file at ${STATE_FILE}..."
        write_state "$THEME_MODE" "$MATUGEN_TYPE" "$MATUGEN_CONTRAST" "$SOURCE_COLOR_INDEX" "$BASE16_BACKEND"
    elif (( STATE_NEEDS_REWRITE )); then
        write_state "$THEME_MODE" "$MATUGEN_TYPE" "$MATUGEN_CONTRAST" "$SOURCE_COLOR_INDEX" "$BASE16_BACKEND"
    else
        write_public_state "$THEME_MODE"
    fi
}

# --- DIRECTORY MANAGEMENT ---

move_directories() {
    local target_mode="$1"
    local source_dir stash_dir

    case "$target_mode" in
        dark)
            source_dir="$STORED_DARK_DIR"
            stash_dir="$STORED_LIGHT_DIR"
            ;;
        light)
            source_dir="$STORED_LIGHT_DIR"
            stash_dir="$STORED_DARK_DIR"
            ;;
        *)
            die "Internal error: invalid mode '${target_mode}'"
            ;;
    esac

    log "Reconciling directories for mode: ${target_mode}"

    ensure_dir "$WALLPAPER_ROOT"

    if [[ -e "$source_dir" && ! -d "$source_dir" ]]; then
        die "FATAL: '${source_dir}' exists but is not a directory."
    fi
    if [[ -e "$stash_dir" && ! -d "$stash_dir" ]]; then
        die "FATAL: '${stash_dir}' exists but is not a directory."
    fi
    if [[ -e "$ACTIVE_THEME_DIR" && ! -d "$ACTIVE_THEME_DIR" ]]; then
        die "FATAL: '${ACTIVE_THEME_DIR}' exists but is not a directory."
    fi

    if [[ -d "$source_dir" ]]; then
        if [[ -d "$ACTIVE_THEME_DIR" ]]; then
            [[ ! -e "$stash_dir" ]] || die "FATAL: Ambiguous state. '${stash_dir}' already exists."
            mv -T -- "$ACTIVE_THEME_DIR" "$stash_dir"
        fi

        [[ ! -e "$ACTIVE_THEME_DIR" ]] || die "FATAL: Destination '${ACTIVE_THEME_DIR}' already exists."
        mv -T -- "$source_dir" "$ACTIVE_THEME_DIR"
    elif [[ ! -d "$ACTIVE_THEME_DIR" ]]; then
        warn "Neither stored '${target_mode}' nor 'active_theme' found."
    fi
}

# --- DAEMON MANAGEMENT ---

wait_for_process() {
    local proc_name="$1"
    local -i attempts=0

    while ! process_running "$proc_name"; do
        (( ++attempts > DAEMON_POLL_LIMIT )) && return 1
        sleep "$DAEMON_POLL_INTERVAL"
    done

    return 0
}

ensure_awww_running() {
    process_running "awww-daemon" && return 0

    log "Starting awww-daemon..."

    if command -v uwsm-app >/dev/null 2>&1; then
        uwsm-app -- awww-daemon --format xrgb >/dev/null 2>&1 99>&- &
    else
        awww-daemon --format xrgb >/dev/null 2>&1 99>&- &
    fi

    wait_for_process "awww-daemon" || die "awww-daemon failed to start"
}

# --- WALLPAPER SELECTION ---

load_wallpapers() {
    local root="$1"
    local recursive="$2"
    local -n out_paths_ref=$3
    local -n out_ids_ref=$4
    local -a found=()
    local record path

    out_paths_ref=()
    out_ids_ref=()

    [[ -d "$root" ]] || return 1

    if [[ "$recursive" == "1" ]]; then
        mapfile -d '' -t found < <(
            find "$root" -type f \
                \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o -iname "*.webp" -o -iname "*.gif" \) \
                -print0 | LC_ALL=C sort -z -V
        )
    else
        mapfile -d '' -t found < <(
            find "$root" -maxdepth 1 -type f \
                \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o -iname "*.webp" -o -iname "*.gif" \) \
                -print0 | LC_ALL=C sort -z -V
        )
    fi

    (( ${#found[@]} > 0 )) || return 1

    out_paths_ref=("${found[@]}")
    for path in "${out_paths_ref[@]}"; do
        out_ids_ref+=( "${path#"$root"/}" )
    done
}

select_wallpaper() {
    local strategy="$1"
    local -n out_path_ref=$2
    local -n out_id_ref=$3

    local track_file last_id=""
    local -i current_index=-1
    local -i selected_index=0
    local -i count=0
    local i
    local -a wallpapers=()
    local -a wallpaper_ids=()

    track_file=$(tracker_file_for_mode "$THEME_MODE")

    if ! load_wallpapers "$ACTIVE_THEME_DIR" 1 wallpapers wallpaper_ids; then
        load_wallpapers "$WALLPAPER_ROOT" 0 wallpapers wallpaper_ids || return 1
    fi

    count=${#wallpapers[@]}

    [[ -f "$track_file" ]] && last_id=$(<"$track_file")

    if [[ -n "$last_id" ]]; then
        for i in "${!wallpaper_ids[@]}"; do
            if [[ "${wallpaper_ids[$i]}" == "$last_id" || "${wallpapers[$i]##*/}" == "$last_id" ]]; then
                current_index=$i
                break
            fi
        done
    fi

    case "$strategy" in
        next)
            if (( current_index >= 0 )); then
                selected_index=$(( current_index + 1 ))
            else
                selected_index=0
            fi
            (( selected_index < count )) || selected_index=0
            ;;
        prev)
            if (( current_index >= 0 )); then
                selected_index=$(( current_index - 1 ))
            else
                selected_index=$(( count - 1 ))
            fi
            (( selected_index >= 0 )) || selected_index=$(( count - 1 ))
            ;;
        random)
            selected_index=$(( SRANDOM % count ))
            ;;
        *)
            die "Internal error: invalid wallpaper selection strategy '${strategy}'"
            ;;
    esac

    out_path_ref="${wallpapers[$selected_index]}"
    out_id_ref="${wallpaper_ids[$selected_index]}"
}

update_wallpaper_tracker() {
    local wallpaper_id="$1"
    local track_file

    track_file=$(tracker_file_for_mode "$THEME_MODE")

    ensure_dir "$STATE_DIR"

    _TEMP_FILE=$(mktemp "${STATE_DIR}/track.XXXXXX")
    printf '%s\n' "$wallpaper_id" > "$_TEMP_FILE"
    mv -fT -- "$_TEMP_FILE" "$track_file"
    _TEMP_FILE=""
}

# --- WALLPAPER / MATUGEN APPLICATION ---

generate_colors() {
    local img="$1"
    local -a cmd
    local output
    local i

    [[ -f "$img" ]] || die "Image file does not exist: $img"

    log "Matugen: Mode=[${THEME_MODE}] Type=[${MATUGEN_TYPE}] Contrast=[${MATUGEN_CONTRAST}] Index=[${SOURCE_COLOR_INDEX}] Base16=[${BASE16_BACKEND}]"

    cmd=(matugen)
    [[ "$BASE16_BACKEND" != "disable" && -n "$BASE16_BACKEND" ]] && cmd+=(--base16-backend "$BASE16_BACKEND")
    cmd+=(--mode "$THEME_MODE")
    [[ "$MATUGEN_TYPE" != "disable" && -n "$MATUGEN_TYPE" ]] && cmd+=(--type "$MATUGEN_TYPE")
    [[ "$MATUGEN_CONTRAST" != "disable" && "$MATUGEN_CONTRAST" != "0" && "$MATUGEN_CONTRAST" != "0.0" && -n "$MATUGEN_CONTRAST" ]] && cmd+=(--contrast "$MATUGEN_CONTRAST")
    cmd+=(--source-color-index "$SOURCE_COLOR_INDEX")
    cmd+=(image "$img")

    if ! output=$("${cmd[@]}" 99>&- 2>&1); then
        if [[ "$output" == *"out of bounds"* ]] && [[ "$SOURCE_COLOR_INDEX" != "0" ]]; then
            warn "Requested color index ${SOURCE_COLOR_INDEX} out of bounds for ${img##*/}. Falling back to index 0."

            for i in "${!cmd[@]}"; do
                if [[ "${cmd[$i]}" == "--source-color-index" ]]; then
                    cmd[$((i + 1))]="0"
                    break
                fi
            done

            if ! output=$("${cmd[@]}" 99>&- 2>&1); then
                die "Matugen generation failed on fallback: $output"
            fi

            SOURCE_COLOR_INDEX="0"
            write_state "$THEME_MODE" "$MATUGEN_TYPE" "$MATUGEN_CONTRAST" "$SOURCE_COLOR_INDEX" "$BASE16_BACKEND"
        else
            die "Matugen generation failed: $output"
        fi
    fi

    if command -v gsettings >/dev/null 2>&1; then
        gsettings set org.gnome.desktop.interface color-scheme "prefer-${THEME_MODE}" 2>/dev/null || true
    fi
}

apply_solid_color() {
    local hex="$1"
    local -a cmd
    local output

    [[ "$hex" =~ ^#?[a-fA-F0-9]{6}$ ]] || die "Invalid HEX color: $hex"
    [[ "$hex" != \#* ]] && hex="#${hex}"

    log "Matugen Solid Color: Hex=[${hex}] Mode=[${THEME_MODE}] Type=[${MATUGEN_TYPE}] Contrast=[${MATUGEN_CONTRAST}] Base16=[${BASE16_BACKEND}]"

    cmd=(matugen)
    [[ "$BASE16_BACKEND" != "disable" && -n "$BASE16_BACKEND" ]] && cmd+=(--base16-backend "$BASE16_BACKEND")
    cmd+=(--mode "$THEME_MODE")
    [[ "$MATUGEN_TYPE" != "disable" && -n "$MATUGEN_TYPE" ]] && cmd+=(--type "$MATUGEN_TYPE")
    [[ "$MATUGEN_CONTRAST" != "disable" && "$MATUGEN_CONTRAST" != "0" && "$MATUGEN_CONTRAST" != "0.0" && -n "$MATUGEN_CONTRAST" ]] && cmd+=(--contrast "$MATUGEN_CONTRAST")
    cmd+=(color hex "$hex")

    if ! output=$("${cmd[@]}" 99>&- 2>&1); then
        die "Matugen color generation failed: $output"
    fi

    if command -v gsettings >/dev/null 2>&1; then
        gsettings set org.gnome.desktop.interface color-scheme "prefer-${THEME_MODE}" 2>/dev/null || true
    fi
}

apply_wallpaper_selection() {
    local strategy="$1"
    local -i do_regen=1
    local wallpaper wallpaper_id

    (( $# > 1 )) && do_regen=$2

    select_wallpaper "$strategy" wallpaper wallpaper_id || die "No wallpapers found in ${ACTIVE_THEME_DIR} or ${WALLPAPER_ROOT}"

    log "Selected: ${wallpaper##*/}"

    ensure_awww_running
    awww img "$wallpaper" \
        --transition-type grow \
        --transition-duration 2 \
        --transition-fps 60 || die "Failed to apply wallpaper with awww"

    update_wallpaper_tracker "$wallpaper_id"

    if (( do_regen )); then
        generate_colors "$wallpaper"
    fi
}

regenerate_current() {
    local query_output line current_wallpaper="" resolved_wallpaper rel_path
    local primary_store secondary_store

    ensure_awww_running

    query_output=$(awww query 2>&1) || die "awww query failed: $query_output"

    while IFS= read -r line; do
        if [[ "$line" == *"currently displaying: image: "* ]]; then
            current_wallpaper="${line##*image: }"
            break
        elif [[ "$line" == *"currently displaying: color: "* ]]; then
            log "awww is displaying a solid color. Automatically falling back to a random wallpaper."
            random_command
            return 0
        fi
    done <<< "$query_output"

    current_wallpaper=$(trim_trailing "$current_wallpaper")
    [[ -n "$current_wallpaper" ]] || die "Could not determine current wallpaper from awww query"

    resolved_wallpaper="$current_wallpaper"

    if [[ ! -f "$resolved_wallpaper" && "$current_wallpaper" == "$ACTIVE_THEME_DIR/"* ]]; then
        rel_path="${current_wallpaper#"$ACTIVE_THEME_DIR"/}"

        if [[ "$THEME_MODE" == "dark" ]]; then
            primary_store="$STORED_LIGHT_DIR"
            secondary_store="$STORED_DARK_DIR"
        else
            primary_store="$STORED_DARK_DIR"
            secondary_store="$STORED_LIGHT_DIR"
        fi

        if [[ -f "${primary_store}/${rel_path}" ]]; then
            resolved_wallpaper="${primary_store}/${rel_path}"
        elif [[ -f "${secondary_store}/${rel_path}" ]]; then
            resolved_wallpaper="${secondary_store}/${rel_path}"
        fi
    fi

    [[ -f "$resolved_wallpaper" ]] || die "Image file does not exist: ${current_wallpaper}"

    if [[ "$resolved_wallpaper" != "$current_wallpaper" ]]; then
        log "Wallpaper moved; resolved to: ${resolved_wallpaper}"
    else
        log "Current wallpaper: ${resolved_wallpaper##*/}"
    fi

    generate_colors "$resolved_wallpaper"
}

# --- CLI ---

usage() {
    cat <<'EOF'
Usage: theme_ctl [COMMAND] [OPTIONS]

Commands:
  set       Update settings and apply changes.
              --mode <light|dark>
              --type <scheme-*|disable>
              --contrast <num[-1..1]|disable>
              --index <n>            Set Matugen source color extraction index
              --base16 <wal|disable> Set Base16 backend generation
              --defaults             Reset all settings to defaults
              --no-wall              Prevent wallpaper change
              --no-regen             Prevent Matugen execution (useful for chaining)
  next      Select the next wallpaper in chronological order.
  prev      Select the previous wallpaper in chronological order.
  random    Select a wallpaper randomly.
  refresh   Regenerate colors for current wallpaper.
  apply     Alias of refresh.
  color     <hex> Generate theme from a solid hex color (e.g., FF0000 or "#FF0000").
  get       Show current configuration.

Examples:
  theme_ctl set --mode dark --index 1 --base16 wal
  theme_ctl set --no-wall --mode light
  theme_ctl next
  theme_ctl prev
  theme_ctl random
  theme_ctl color FF0000
EOF
}

cmd_get() {
    cat "$STATE_FILE"
    printf '\n# Public State (%s):\n' "$PUBLIC_STATE_FILE"
    if [[ -f "$PUBLIC_STATE_FILE" ]]; then
        cat "$PUBLIC_STATE_FILE"
    else
        printf 'N/A\n'
    fi
}

cmd_set() {
    local current_mode="$THEME_MODE"
    local current_type="$MATUGEN_TYPE"
    local current_contrast="$MATUGEN_CONTRAST"
    local current_index="$SOURCE_COLOR_INDEX"
    local current_base16="$BASE16_BACKEND"

    local desired_mode="$THEME_MODE"
    local desired_type="$MATUGEN_TYPE"
    local desired_contrast="$MATUGEN_CONTRAST"
    local desired_index="$SOURCE_COLOR_INDEX"
    local desired_base16="$BASE16_BACKEND"

    local mode_request_kind=""
    local -i settings_changed=0
    local -i mode_changed=0
    local -i same_mode_requested=0
    local -i skip_wall=0
    local -i skip_regen=0
    local -i need_wall=0
    local -i need_regen=0
    local -i full_state_pending=0

    while (( $# > 0 )); do
        case "$1" in
            --mode)
                [[ -n "${2:-}" ]] || die "--mode requires a value"
                [[ "$2" == "light" || "$2" == "dark" ]] || die "--mode must be 'light' or 'dark'"
                desired_mode="$2"
                mode_request_kind="explicit"
                shift 2
                ;;
            --type)
                [[ -n "${2:-}" ]] || die "--type requires a value"
                is_valid_matugen_type "$2" || die "--type must be one of: disable, scheme-content, scheme-expressive, scheme-fidelity, scheme-fruit-salad, scheme-monochrome, scheme-neutral, scheme-rainbow, scheme-tonal-spot, scheme-vibrant"
                desired_type="$2"
                shift 2
                ;;
            --contrast)
                [[ -n "${2:-}" ]] || die "--contrast requires a value"
                is_valid_contrast "$2" || die "--contrast must be 'disable' or a numeric value in the range [-1, 1]"
                desired_contrast="$2"
                shift 2
                ;;
            --index)
                [[ -n "${2:-}" ]] || die "--index requires a value (e.g., 0, 1, 2)"
                [[ "$2" =~ ^[0-9]+$ ]] || die "--index must be a non-negative integer"
                desired_index="$2"
                shift 2
                ;;
            --base16)
                [[ -n "${2:-}" ]] || die "--base16 requires a value (e.g., wal, disable)"
                is_valid_base16_backend "$2" || die "--base16 must be 'wal' or 'disable'"
                desired_base16="$2"
                shift 2
                ;;
            --defaults)
                desired_mode="$DEFAULT_MODE"
                desired_type="$DEFAULT_TYPE"
                desired_contrast="$DEFAULT_CONTRAST"
                desired_index="$DEFAULT_COLOR_INDEX"
                desired_base16="$DEFAULT_BASE16"
                mode_request_kind="defaults"
                shift
                ;;
            --no-wall)
                skip_wall=1
                shift
                ;;
            --no-regen)
                skip_regen=1
                shift
                ;;
            --help)
                usage
                exit 0
                ;;
            *)
                die "Unknown option: $1"
                ;;
        esac
    done

    [[ "$desired_mode" != "$current_mode" ]] && mode_changed=1

    if [[ "$desired_type" != "$current_type" || "$desired_contrast" != "$current_contrast" || "$desired_index" != "$current_index" || "$desired_base16" != "$current_base16" ]]; then
        settings_changed=1
    fi

    if [[ "$mode_request_kind" == "explicit" && "$desired_mode" == "$current_mode" ]]; then
        same_mode_requested=1
    fi

    if (( ! skip_wall )) && (( mode_changed || same_mode_requested )); then
        need_wall=1
    fi

    if (( ! skip_regen )) && (( settings_changed || same_mode_requested || mode_changed )); then
        need_regen=1
    fi

    if (( mode_changed )); then
        move_directories "$desired_mode"

        if (( settings_changed && !skip_regen )); then
            write_state "$desired_mode" "$current_type" "$current_contrast" "$current_index" "$current_base16"
            full_state_pending=1
        else
            write_state "$desired_mode" "$desired_type" "$desired_contrast" "$desired_index" "$desired_base16"
        fi
    fi

    THEME_MODE="$desired_mode"
    MATUGEN_TYPE="$desired_type"
    MATUGEN_CONTRAST="$desired_contrast"
    SOURCE_COLOR_INDEX="$desired_index"
    BASE16_BACKEND="$desired_base16"

    if (( ! mode_changed && settings_changed && skip_regen )); then
        write_state "$THEME_MODE" "$MATUGEN_TYPE" "$MATUGEN_CONTRAST" "$SOURCE_COLOR_INDEX" "$BASE16_BACKEND"
    elif (( ! mode_changed && settings_changed )); then
        full_state_pending=1
    fi

    if (( need_wall )); then
        apply_wallpaper_selection next "$(( ! skip_regen ))"
    elif (( need_regen )); then
        regenerate_current
    fi

    if (( full_state_pending )); then
        write_state "$THEME_MODE" "$MATUGEN_TYPE" "$MATUGEN_CONTRAST" "$SOURCE_COLOR_INDEX" "$BASE16_BACKEND"
    fi
}

next_command() {
    move_directories "$THEME_MODE"
    apply_wallpaper_selection next 1
}

prev_command() {
    move_directories "$THEME_MODE"
    apply_wallpaper_selection prev 1
}

random_command() {
    move_directories "$THEME_MODE"
    apply_wallpaper_selection random 1
}

run_locked() {
    local fn="$1"
    shift

    ensure_dir "${LOCK_FILE%/*}"

    exec 99>> "$LOCK_FILE"
    flock -w "$FLOCK_TIMEOUT_SEC" -x 99 || die "Could not acquire lock"

    init_state
    "$fn" "$@"

    exec 99>&- 2>/dev/null || true
}

# --- MAIN ---

case "${1:-}" in
    set)
        shift
        if (( $# == 1 )) && [[ "$1" == "--help" ]]; then
            usage
            exit 0
        fi
        check_deps flock awk pgrep find sort awww awww-daemon matugen
        run_locked cmd_set "$@"
        ;;
    next)
        check_deps flock awk pgrep find sort awww awww-daemon matugen
        run_locked next_command
        ;;
    prev|previous)
        check_deps flock awk pgrep find sort awww awww-daemon matugen
        run_locked prev_command
        ;;
    random)
        check_deps flock awk pgrep find sort awww awww-daemon matugen
        run_locked random_command
        ;;
    refresh|apply)
        check_deps flock awk pgrep awww awww-daemon matugen
        run_locked regenerate_current
        ;;
    color)
        shift
        [[ -n "${1:-}" ]] || die "color command requires a hex value (e.g., FF0000 or \"#FF0000\")"
        hex_val="$1"
        check_deps flock awk pgrep matugen
        run_locked apply_solid_color "$hex_val"
        ;;
    get)
        check_deps flock awk
        run_locked cmd_get
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
