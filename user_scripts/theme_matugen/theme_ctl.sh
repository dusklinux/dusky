#!/usr/bin/env bash
# ==============================================================================
# THEME CONTROLLER (theme_ctl)
# ==============================================================================
# Description: Centralized state manager for system theming.
#              Handles Matugen config, physical directory swaps, and wallpaper updates.
#              Provides full animation support for awww daemon.
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
#   theme_ctl set --trans-type wave --trans-duration 2.5
#   theme_ctl next --no-regen
#   theme_ctl prev
#   theme_ctl random --no-regen
#   theme_ctl refresh
#   theme_ctl color FF0000
#   theme_ctl get
# ==============================================================================

if (( BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 1) )); then
    printf '\033[1;31mERROR:\033[0m This script requires Bash 5.1+ for SRANDOM support (Current: %s).\n' "${BASH_VERSION}" >&2
    exit 1
fi

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

# State Defaults
readonly DEFAULT_MODE="dark"
readonly DEFAULT_TYPE="scheme-tonal-spot"
readonly DEFAULT_CONTRAST="0"
readonly DEFAULT_COLOR_INDEX="0"
readonly DEFAULT_BASE16="disable"

# awww Animation Defaults
readonly DEFAULT_TRANS_TYPE="random"
readonly DEFAULT_TRANS_DURATION="2"
readonly DEFAULT_TRANS_FPS="60"
readonly DEFAULT_TRANS_ANGLE="30"
readonly DEFAULT_TRANS_POS="center"
readonly DEFAULT_TRANS_BEZIER=".54,0,.34,.99"

readonly DAEMON_POLL_INTERVAL=0.1
readonly DAEMON_POLL_LIMIT=50

# --- STATE VARIABLES ---
THEME_MODE=""
MATUGEN_TYPE=""
MATUGEN_CONTRAST=""
SOURCE_COLOR_INDEX=""
BASE16_BACKEND=""
AWWW_TRANS_TYPE=""
AWWW_TRANS_DURATION=""
AWWW_TRANS_FPS=""
AWWW_TRANS_BEZIER=""
AWWW_TRANS_ANGLE=""
AWWW_TRANS_POS=""

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
        disable|scheme-content|scheme-expressive|scheme-fidelity|scheme-fruit-salad|scheme-monochrome|scheme-neutral|scheme-rainbow|scheme-tonal-spot|scheme-vibrant) return 0 ;;
        *) return 1 ;;
    esac
}

is_valid_contrast() {
    local value="$1"
    [[ "$value" == "disable" ]] && return 0
    # Pure native Bash float validation for [-1, 1] range 
    # (Updated to safely handle leading zeros like 00.5 or 01.0 just like the old script did)
    [[ "$value" =~ ^[+-]?(0*1(\.0*)?|0*\.[0-9]+|0+|\.[0-9]+)$ ]] && return 0
    return 1
}

is_valid_base16_backend() {
    [[ "$1" == "disable" || "$1" == "wal" ]]
}

is_valid_bezier() {
    local val="$1"
    [[ "$val" == "disable" ]] && return 0
    [[ "$val" =~ ^[+-]?[0-9]*\.?[0-9]+,[+-]?[0-9]*\.?[0-9]+,[+-]?[0-9]*\.?[0-9]+,[+-]?[0-9]*\.?[0-9]+$ ]] && return 0
    return 1
}

is_valid_angle() {
    local val="$1"
    [[ "$val" == "disable" ]] && return 0
    [[ "$val" =~ ^[+-]?[0-9]+([.][0-9]+)?$ ]] && return 0
    return 1
}

is_valid_pos() {
    local val="$1"
    [[ "$val" == "disable" ]] && return 0
    case "$val" in
        center|top|left|right|bottom|top-left|top-right|bottom-left|bottom-right) return 0 ;;
    esac
    [[ "$val" =~ ^[+-]?[0-9]*\.?[0-9]+,[+-]?[0-9]*\.?[0-9]+$ ]] && return 0
    return 1
}

resolve_wallpaper_id() {
    local full_path="$1"
    local abs_path
    abs_path=$(realpath -s "$full_path" 2>/dev/null || readlink -f "$full_path" || echo "$full_path")

    local active_dir_clean="${ACTIVE_THEME_DIR%/}"
    local root_dir_clean="${WALLPAPER_ROOT%/}"

    if [[ "$abs_path" == "$active_dir_clean"/* ]]; then
        printf '%s\n' "${abs_path#"$active_dir_clean"/}"
    elif [[ "$abs_path" == "$root_dir_clean"/* ]]; then
        printf '%s\n' "${abs_path#"$root_dir_clean"/}"
    else
        basename "$abs_path"
    fi
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
    AWWW_TRANS_TYPE="$DEFAULT_TRANS_TYPE"
    AWWW_TRANS_DURATION="$DEFAULT_TRANS_DURATION"
    AWWW_TRANS_FPS="$DEFAULT_TRANS_FPS"
    AWWW_TRANS_BEZIER="$DEFAULT_TRANS_BEZIER"
    AWWW_TRANS_ANGLE="$DEFAULT_TRANS_ANGLE"
    AWWW_TRANS_POS="$DEFAULT_TRANS_POS"
    STATE_NEEDS_REWRITE=0

    local -A found_keys=()
    local key value

    if [[ ! -f "$STATE_FILE" ]]; then
        STATE_NEEDS_REWRITE=1
        return 0
    fi

    while IFS= read -r line || [[ -n "$line" ]]; do
        # Fast native trim
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"

        [[ -z "$line" || "${line:0:1}" == "#" ]] && continue

        key="${line%%=*}"
        value="${line#*=}"
        
        # Trim internal key/values in case of dirty edits (e.g. KEY  =  VALUE)
        key="${key%"${key##*[![:space:]]}"}"
        value="${value#"${value%%[![:space:]]*}"}"

        # Strip surrounding quotes safely
        if [[ ${#value} -ge 2 ]]; then
            if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]] || [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
                value="${value:1:-1}"
            fi
        fi

        case "$key" in
            THEME_MODE|MATUGEN_TYPE|MATUGEN_CONTRAST|SOURCE_COLOR_INDEX|BASE16_BACKEND|AWWW_TRANS_TYPE|AWWW_TRANS_DURATION|AWWW_TRANS_FPS|AWWW_TRANS_BEZIER|AWWW_TRANS_ANGLE|AWWW_TRANS_POS)
                printf -v "$key" "%s" "$value"
                found_keys["$key"]=1
                ;;
        esac
    done < "$STATE_FILE"

    # Core Validations
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
    
    # Animation Validations
    case "$AWWW_TRANS_TYPE" in
        disable|none|simple|fade|left|right|top|bottom|wipe|wave|grow|center|any|outer|random) ;;
        *)
            warn "Invalid AWWW_TRANS_TYPE. Resetting to ${DEFAULT_TRANS_TYPE}."
            AWWW_TRANS_TYPE="$DEFAULT_TRANS_TYPE"
            STATE_NEEDS_REWRITE=1
            ;;
    esac

    if [[ "$AWWW_TRANS_DURATION" != "disable" && ! "$AWWW_TRANS_DURATION" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        warn "Invalid AWWW_TRANS_DURATION. Resetting."
        AWWW_TRANS_DURATION="$DEFAULT_TRANS_DURATION"
        STATE_NEEDS_REWRITE=1
    fi

    if [[ "$AWWW_TRANS_FPS" != "disable" && ! "$AWWW_TRANS_FPS" =~ ^[0-9]+$ ]]; then
        warn "Invalid AWWW_TRANS_FPS. Resetting."
        AWWW_TRANS_FPS="$DEFAULT_TRANS_FPS"
        STATE_NEEDS_REWRITE=1
    fi

    if ! is_valid_bezier "$AWWW_TRANS_BEZIER"; then
        warn "Invalid AWWW_TRANS_BEZIER. Resetting."
        AWWW_TRANS_BEZIER="$DEFAULT_TRANS_BEZIER"
        STATE_NEEDS_REWRITE=1
    fi

    if ! is_valid_angle "$AWWW_TRANS_ANGLE"; then
        warn "Invalid AWWW_TRANS_ANGLE. Resetting."
        AWWW_TRANS_ANGLE="$DEFAULT_TRANS_ANGLE"
        STATE_NEEDS_REWRITE=1
    fi

    if ! is_valid_pos "$AWWW_TRANS_POS"; then
        warn "Invalid AWWW_TRANS_POS. Resetting."
        AWWW_TRANS_POS="$DEFAULT_TRANS_POS"
        STATE_NEEDS_REWRITE=1
    fi

    local required=(THEME_MODE MATUGEN_TYPE MATUGEN_CONTRAST SOURCE_COLOR_INDEX BASE16_BACKEND AWWW_TRANS_TYPE AWWW_TRANS_DURATION AWWW_TRANS_FPS AWWW_TRANS_BEZIER AWWW_TRANS_ANGLE AWWW_TRANS_POS)
    for req in "${required[@]}"; do
        if [[ -z "${found_keys[$req]:-}" ]]; then
            STATE_NEEDS_REWRITE=1
        fi
    done
}

write_state() {
    # Strictly define the canonical ordering for the configuration file
    local -a key_order=(
        THEME_MODE
        MATUGEN_TYPE
        MATUGEN_CONTRAST
        SOURCE_COLOR_INDEX
        BASE16_BACKEND
        AWWW_TRANS_TYPE
        AWWW_TRANS_DURATION
        AWWW_TRANS_FPS
        AWWW_TRANS_BEZIER
        AWWW_TRANS_ANGLE
        AWWW_TRANS_POS
    )

    local -A current_state=(
        [THEME_MODE]="$THEME_MODE"
        [MATUGEN_TYPE]="$MATUGEN_TYPE"
        [MATUGEN_CONTRAST]="$MATUGEN_CONTRAST"
        [SOURCE_COLOR_INDEX]="$SOURCE_COLOR_INDEX"
        [BASE16_BACKEND]="$BASE16_BACKEND"
        [AWWW_TRANS_TYPE]="$AWWW_TRANS_TYPE"
        [AWWW_TRANS_DURATION]="$AWWW_TRANS_DURATION"
        [AWWW_TRANS_FPS]="$AWWW_TRANS_FPS"
        [AWWW_TRANS_BEZIER]="$AWWW_TRANS_BEZIER"
        [AWWW_TRANS_ANGLE]="$AWWW_TRANS_ANGLE"
        [AWWW_TRANS_POS]="$AWWW_TRANS_POS"
    )

    ensure_dir "$STATE_DIR"
    _TEMP_FILE=$(mktemp "${STATE_DIR}/state.conf.XXXXXX")

    if [[ -s "$STATE_FILE" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            # Safely trim for evaluation without destroying user formatting
            local eval_line="${line#"${line%%[![:space:]]*}"}"
            
            if [[ -z "$eval_line" || "${eval_line:0:1}" == "#" ]]; then
                printf '%s\n' "$line"
                continue
            fi

            local key="${eval_line%%=*}"
            key="${key%"${key##*[![:space:]]}"}"

            if [[ -n "$key" ]] && [[ -v current_state["$key"] ]]; then
                printf '%s="%s"\n' "$key" "${current_state[$key]}"
                unset 'current_state['"$key"']'
            else
                printf '%s\n' "$line"
            fi
        done < "$STATE_FILE" > "$_TEMP_FILE"
    else
        printf '# Dusky Theme State File\n' > "$_TEMP_FILE"
    fi

    # Append any totally new/missing keys according to our STRICT canonical order
    for key in "${key_order[@]}"; do
        if [[ -v current_state["$key"] ]]; then
            printf '%s="%s"\n' "$key" "${current_state[$key]}"
        fi
    done >> "$_TEMP_FILE"

    mv -fT -- "$_TEMP_FILE" "$STATE_FILE"
    _TEMP_FILE=""

    write_public_state "$THEME_MODE"
    STATE_NEEDS_REWRITE=0
}

init_state() {
    ensure_dir "$STATE_DIR"
    read_state

    if [[ ! -s "$STATE_FILE" ]] || (( STATE_NEEDS_REWRITE )); then
        if [[ ! -s "$STATE_FILE" ]]; then
            log "Initializing new state file at ${STATE_FILE}..."
        fi
        write_state
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
        # Utilizing 99>&- to ensure uwsm/awww don't inherit the flock descriptor
        uwsm-app -- awww-daemon --format xrgb >/dev/null 2>&1 99>&- &
    else
        awww-daemon --format xrgb >/dev/null 2>&1 99>&- &
        disown $! 2>/dev/null || true
    fi

    wait_for_process "awww-daemon" || die "awww-daemon failed to start"
}

# --- WALLPAPER SELECTION ---

load_wallpapers() {
    local root="${1%/}"
    local recursive="$2"
    # shellcheck disable=SC2034
    local -n out_paths_ref=$3
    # shellcheck disable=SC2034
    local -n out_ids_ref=$4
    local -a found=()
    local path

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
    # shellcheck disable=SC2034
    local -n out_path_ref=$2
    # shellcheck disable=SC2034
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

    # shellcheck disable=SC2034
    out_path_ref="${wallpapers[$selected_index]}"
    # shellcheck disable=SC2034
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

    # STRICT CLAP ALIGNMENT: Binary -> Global Options -> Subcommand -> Positional Args
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
                    cmd[i + 1]="0"
                    break
                fi
            done

            if ! output=$("${cmd[@]}" 99>&- 2>&1); then
                die "Matugen generation failed on fallback: $output"
            fi

            SOURCE_COLOR_INDEX="0"
            write_state
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

    # STRICT CLAP ALIGNMENT: Binary -> Global Options -> Subcommand -> Sub-arguments
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

apply_wallpaper_direct() {
    local img_path="$1"
    local -i do_regen=1
    local wallpaper_id

    (( $# > 1 )) && do_regen=$2

    [[ -f "$img_path" ]] || die "Wallpaper file does not exist: $img_path"

    wallpaper_id=$(resolve_wallpaper_id "$img_path")

    log "Applying selected wallpaper: ${img_path##*/} [Trans: ${AWWW_TRANS_TYPE}]"

    ensure_awww_running

    local -a awww_cmd=(awww img)
    [[ -n "$AWWW_TRANS_TYPE" && "$AWWW_TRANS_TYPE" != "disable" ]] && awww_cmd+=(--transition-type "$AWWW_TRANS_TYPE")
    [[ -n "$AWWW_TRANS_DURATION" && "$AWWW_TRANS_DURATION" != "disable" ]] && awww_cmd+=(--transition-duration "$AWWW_TRANS_DURATION")
    [[ -n "$AWWW_TRANS_FPS" && "$AWWW_TRANS_FPS" != "disable" ]] && awww_cmd+=(--transition-fps "$AWWW_TRANS_FPS")
    [[ -n "$AWWW_TRANS_ANGLE" && "$AWWW_TRANS_ANGLE" != "disable" ]] && awww_cmd+=(--transition-angle "$AWWW_TRANS_ANGLE")
    [[ -n "$AWWW_TRANS_POS" && "$AWWW_TRANS_POS" != "disable" ]] && awww_cmd+=(--transition-pos "$AWWW_TRANS_POS")
    [[ -n "$AWWW_TRANS_BEZIER" && "$AWWW_TRANS_BEZIER" != "disable" ]] && awww_cmd+=(--transition-bezier "$AWWW_TRANS_BEZIER")

    awww_cmd+=("$img_path")

    "${awww_cmd[@]}" 99>&- || die "Failed to apply wallpaper with awww"

    update_wallpaper_tracker "$wallpaper_id"

    if (( do_regen )); then
        generate_colors "$img_path"
    fi
}

apply_wallpaper_selection() {
    local strategy="$1"
    local -i do_regen=1
    local wallpaper wallpaper_id

    (( $# > 1 )) && do_regen=$2

    select_wallpaper "$strategy" wallpaper wallpaper_id || die "No wallpapers found in ${ACTIVE_THEME_DIR} or ${WALLPAPER_ROOT}"

    log "Selected: ${wallpaper##*/} [Trans: ${AWWW_TRANS_TYPE}]"

    ensure_awww_running
    
    # STRICT CLAP ALIGNMENT: `awww img [OPTIONS] <IMAGE>`
    local -a awww_cmd=(awww img)
    [[ -n "$AWWW_TRANS_TYPE" && "$AWWW_TRANS_TYPE" != "disable" ]] && awww_cmd+=(--transition-type "$AWWW_TRANS_TYPE")
    [[ -n "$AWWW_TRANS_DURATION" && "$AWWW_TRANS_DURATION" != "disable" ]] && awww_cmd+=(--transition-duration "$AWWW_TRANS_DURATION")
    [[ -n "$AWWW_TRANS_FPS" && "$AWWW_TRANS_FPS" != "disable" ]] && awww_cmd+=(--transition-fps "$AWWW_TRANS_FPS")
    [[ -n "$AWWW_TRANS_ANGLE" && "$AWWW_TRANS_ANGLE" != "disable" ]] && awww_cmd+=(--transition-angle "$AWWW_TRANS_ANGLE")
    [[ -n "$AWWW_TRANS_POS" && "$AWWW_TRANS_POS" != "disable" ]] && awww_cmd+=(--transition-pos "$AWWW_TRANS_POS")
    [[ -n "$AWWW_TRANS_BEZIER" && "$AWWW_TRANS_BEZIER" != "disable" ]] && awww_cmd+=(--transition-bezier "$AWWW_TRANS_BEZIER")

    awww_cmd+=("$wallpaper")

    "${awww_cmd[@]}" 99>&- || die "Failed to apply wallpaper with awww"

    update_wallpaper_tracker "$wallpaper_id"

    if (( do_regen )); then
        generate_colors "$wallpaper"
    fi
}

# shellcheck disable=SC2120
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
            random_command "$@"
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

    if [[ ! -f "$resolved_wallpaper" ]]; then
        warn "Current wallpaper '${current_wallpaper}' not found. Selecting a random wallpaper."
        random_command "$@"
        return 0
    fi

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
              --trans-type <type>    Animation type (random, grow, wipe, wave, etc.)
              --trans-duration <sec> Animation duration
              --trans-fps <fps>      Animation frames per second
              --trans-bezier <bez>   Animation bezier curve (e.g., .54,0,.34,.99)
              --trans-angle <deg>    Animation angle (for wave, wipe)
              --trans-pos <pos>      Animation position (e.g., center, top-left)
              --defaults             Reset all settings to defaults
              --no-wall              Prevent wallpaper change
              --no-regen             Prevent Matugen execution (useful for chaining)
  next      [--no-regen] Select the next wallpaper.
  prev      [--no-regen] Select the previous wallpaper.
  random    [--no-regen] Select a random wallpaper.
  refresh   Regenerate colors for current wallpaper.
  apply     Alias of refresh.
  color     <hex> Generate theme from a solid hex color (e.g., FF0000 or "#FF0000").
  get       Show current configuration.

Examples:
  theme_ctl set --mode dark --trans-type wave --trans-duration 2.5
  theme_ctl next --no-regen
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
    # Isolate exact diffs
    local prev_mode="$THEME_MODE"
    local prev_type="$MATUGEN_TYPE"
    local prev_contrast="$MATUGEN_CONTRAST"
    local prev_index="$SOURCE_COLOR_INDEX"
    local prev_base16="$BASE16_BACKEND"

    local mode_request_kind=""
    local -i skip_wall=0
    local -i skip_regen=0
    local WALLPAPER_PATH=""

    while (( $# > 0 )); do
        case "$1" in
            --mode)
                [[ -n "${2:-}" ]] || die "--mode requires a value"
                [[ "$2" == "light" || "$2" == "dark" ]] || die "--mode must be 'light' or 'dark'"
                THEME_MODE="$2"
                mode_request_kind="explicit"
                shift 2
                ;;
            --type)
                [[ -n "${2:-}" ]] || die "--type requires a value"
                is_valid_matugen_type "$2" || die "--type must be valid"
                MATUGEN_TYPE="$2"
                shift 2
                ;;
            --contrast)
                [[ -n "${2:-}" ]] || die "--contrast requires a value"
                is_valid_contrast "$2" || die "--contrast must be valid"
                MATUGEN_CONTRAST="$2"
                shift 2
                ;;
            --index)
                [[ -n "${2:-}" ]] || die "--index requires a value"
                [[ "$2" =~ ^[0-9]+$ ]] || die "--index must be non-negative integer"
                SOURCE_COLOR_INDEX="$2"
                shift 2
                ;;
            --base16)
                [[ -n "${2:-}" ]] || die "--base16 requires a value"
                is_valid_base16_backend "$2" || die "--base16 must be 'wal' or 'disable'"
                BASE16_BACKEND="$2"
                shift 2
                ;;
            --trans-type)
                [[ -n "${2:-}" ]] || die "--trans-type requires a value"
                case "$2" in
                    disable|none|simple|fade|left|right|top|bottom|wipe|wave|grow|center|any|outer|random) ;;
                    *) die "--trans-type must be a valid transition type" ;;
                esac
                AWWW_TRANS_TYPE="$2"
                shift 2
                ;;
            --trans-duration)
                [[ -n "${2:-}" ]] || die "--trans-duration requires a value"
                [[ "$2" == "disable" || "$2" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "--trans-duration must be a positive number or 'disable'"
                AWWW_TRANS_DURATION="$2"
                shift 2
                ;;
            --trans-fps)
                [[ -n "${2:-}" ]] || die "--trans-fps requires a value"
                [[ "$2" == "disable" || "$2" =~ ^[0-9]+$ ]] || die "--trans-fps must be an integer or 'disable'"
                AWWW_TRANS_FPS="$2"
                shift 2
                ;;
            --trans-bezier)
                [[ -n "${2:-}" ]] || die "--trans-bezier requires a value"
                is_valid_bezier "$2" || die "--trans-bezier must be a valid cubic-bezier (e.g. .54,0,.34,.99)"
                AWWW_TRANS_BEZIER="$2"
                shift 2
                ;;
            --trans-angle)
                [[ -n "${2:-}" ]] || die "--trans-angle requires a value"
                is_valid_angle "$2" || die "--trans-angle must be a valid number or 'disable'"
                AWWW_TRANS_ANGLE="$2"
                shift 2
                ;;
            --trans-pos)
                [[ -n "${2:-}" ]] || die "--trans-pos requires a value"
                is_valid_pos "$2" || die "--trans-pos must be a valid position alias or coordinates"
                AWWW_TRANS_POS="$2"
                shift 2
                ;;
            --defaults)
                THEME_MODE="$DEFAULT_MODE"
                MATUGEN_TYPE="$DEFAULT_TYPE"
                MATUGEN_CONTRAST="$DEFAULT_CONTRAST"
                SOURCE_COLOR_INDEX="$DEFAULT_COLOR_INDEX"
                BASE16_BACKEND="$DEFAULT_BASE16"
                AWWW_TRANS_TYPE="$DEFAULT_TRANS_TYPE"
                AWWW_TRANS_DURATION="$DEFAULT_TRANS_DURATION"
                AWWW_TRANS_FPS="$DEFAULT_TRANS_FPS"
                AWWW_TRANS_BEZIER="$DEFAULT_TRANS_BEZIER"
                AWWW_TRANS_ANGLE="$DEFAULT_TRANS_ANGLE"
                AWWW_TRANS_POS="$DEFAULT_TRANS_POS"
                mode_request_kind="defaults"
                shift
                ;;
            --no-wall) skip_wall=1; shift ;;
            --no-regen) skip_regen=1; shift ;;
            --help) usage; exit 0 ;;
            -*) die "Unknown option: $1" ;;
            *)
                if [[ -z "$WALLPAPER_PATH" ]]; then
                    WALLPAPER_PATH="$1"
                    shift
                else
                    die "Unknown argument: $1"
                fi
                ;;
        esac
    done

    # Boolean mapping for routing logic
    local -i mode_changed=0
    local -i matugen_settings_changed=0
    local -i same_mode_requested=0

    [[ "$THEME_MODE" != "$prev_mode" ]] && mode_changed=1
    
    if [[ "$MATUGEN_TYPE" != "$prev_type" || "$MATUGEN_CONTRAST" != "$prev_contrast" || \
          "$SOURCE_COLOR_INDEX" != "$prev_index" || "$BASE16_BACKEND" != "$prev_base16" ]]; then
        matugen_settings_changed=1
    fi

    if [[ "$mode_request_kind" == "explicit" && "$THEME_MODE" == "$prev_mode" ]]; then
        same_mode_requested=1
    fi

    if (( mode_changed )); then
        move_directories "$THEME_MODE"
    fi

    write_state

    # Execute
    if [[ -n "$WALLPAPER_PATH" ]]; then
        apply_wallpaper_direct "$WALLPAPER_PATH" "$(( ! skip_regen ))"
    elif (( ! skip_wall )) && (( mode_changed || same_mode_requested )); then
        apply_wallpaper_selection next "$(( ! skip_regen ))"
    elif (( ! skip_regen )) && (( matugen_settings_changed || same_mode_requested || mode_changed )); then
        regenerate_current
    fi
}

next_command() {
    local -i do_regen=1
    for arg in "$@"; do [[ "$arg" == "--no-regen" ]] && do_regen=0; done
    move_directories "$THEME_MODE"
    apply_wallpaper_selection next "$do_regen"
}

prev_command() {
    local -i do_regen=1
    for arg in "$@"; do [[ "$arg" == "--no-regen" ]] && do_regen=0; done
    move_directories "$THEME_MODE"
    apply_wallpaper_selection prev "$do_regen"
}

random_command() {
    local -i do_regen=1
    for arg in "$@"; do [[ "$arg" == "--no-regen" ]] && do_regen=0; done
    move_directories "$THEME_MODE"
    apply_wallpaper_selection random "$do_regen"
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
        check_deps flock pgrep find sort awww awww-daemon matugen
        run_locked cmd_set "$@"
        ;;
    next)
        shift
        check_deps flock pgrep find sort awww awww-daemon matugen
        run_locked next_command "$@"
        ;;
    prev|previous)
        shift
        check_deps flock pgrep find sort awww awww-daemon matugen
        run_locked prev_command "$@"
        ;;
    random)
        shift
        check_deps flock pgrep find sort awww awww-daemon matugen
        run_locked random_command "$@"
        ;;
    refresh|apply)
        check_deps flock pgrep awww awww-daemon matugen
        run_locked regenerate_current
        ;;
    color)
        shift
        [[ -n "${1:-}" ]] || die "color command requires a hex value (e.g., FF0000 or \"#FF0000\")"
        hex_val="$1"
        check_deps flock pgrep matugen
        run_locked apply_solid_color "$hex_val"
        ;;
    get)
        check_deps flock
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
