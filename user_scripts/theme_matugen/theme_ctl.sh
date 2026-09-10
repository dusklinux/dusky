#!/usr/bin/env bash
# ==============================================================================
# THEME CONTROLLER — theme_ctl
# ==============================================================================
#
# Arch Linux / Bash / Wayland / awww / Matugen
#
# No flock, lock files, or persistent controller worker.
#
# CONCURRENCY:
#   Independent invocations are NOT serialized.
#   Avoid overlapping mode changes and Matugen generation.
#   For rapid wallpaper-only keybinds, use:
#       theme_ctl next --no-regen
#
# STATE:
#   state.conf     Desired settings; not an all-components-applied marker.
#   state          true = dark, false = light.
#   light_wal      Last light-mode wallpaper identifier.
#   dark_wal       Last dark-mode wallpaper identifier.
#   current_image  NUL-separated reported/source image paths.
#
# SEMANTICS:
#   --no-wall      Do not issue a wallpaper-changing command.
#                  An explicit image may still supply Matugen colors.
#                  Mode-directory reconciliation remains enabled.
#   --no-regen     Do not execute Matugen.
#   disable        Omit the corresponding backend option. Backend defaults
#                  and configuration can still affect behavior.
#
# DIRECTORY OPERATIONS:
#   Physical theme slots must not be symlinks.
#   GNU mv --no-copy is required for directory swaps: no copy/delete fallback.
#   A failed second move triggers an attempted rollback.
#   Directory moves, state writes, and external applications are not one
#   crash-atomic transaction.
#
# Matugen configuration/hooks must not independently change wallpapers if
# --no-wall is expected to preserve the displayed wallpaper.
#
# ==============================================================================

if (( BASH_VERSINFO[0] < 5 ||
      (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 1) )); then
    printf 'ERROR: Bash 5.1+ is required; running %s.\n' "$BASH_VERSION" >&2
    exit 1
fi

set -euo pipefail

# --- CONFIGURATION ------------------------------------------------------------

readonly STATE_DIR="${HOME:?HOME is not set}/.config/dusky/settings/dusky_theme"
readonly STATE_FILE="${STATE_DIR}/state.conf"
readonly PUBLIC_STATE_FILE="${STATE_DIR}/state"
readonly TRACK_LIGHT="${STATE_DIR}/light_wal"
readonly TRACK_DARK="${STATE_DIR}/dark_wal"
readonly CURRENT_IMAGE_FILE="${STATE_DIR}/current_image"

readonly BASE_PICTURES="${HOME}/Pictures"
readonly STORED_LIGHT_DIR="${BASE_PICTURES}/light"
readonly STORED_DARK_DIR="${BASE_PICTURES}/dark"
readonly WALLPAPER_ROOT="${BASE_PICTURES}/wallpapers"
readonly ACTIVE_THEME_DIR="${WALLPAPER_ROOT}/active_theme"

readonly AWWW_QUERY_TIMEOUT_SEC=2
readonly DAEMON_START_TIMEOUT_SEC=5
readonly DAEMON_POLL_INTERVAL=0.1

readonly -a STATE_KEYS=(
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

readonly -A DEFAULTS=(
    [THEME_MODE]="dark"
    [MATUGEN_TYPE]="scheme-tonal-spot"
    [MATUGEN_CONTRAST]="0"
    [SOURCE_COLOR_INDEX]="0"
    [BASE16_BACKEND]="disable"
    [AWWW_TRANS_TYPE]="random"
    [AWWW_TRANS_DURATION]="2"
    [AWWW_TRANS_FPS]="60"
    [AWWW_TRANS_BEZIER]=".54,0,.34,.99"
    [AWWW_TRANS_ANGLE]="30"
    [AWWW_TRANS_POS]="center"
)

readonly -A OPTION_KEYS=(
    [--mode]=THEME_MODE
    [--type]=MATUGEN_TYPE
    [--contrast]=MATUGEN_CONTRAST
    [--index]=SOURCE_COLOR_INDEX
    [--base16]=BASE16_BACKEND
    [--trans-type]=AWWW_TRANS_TYPE
    [--trans-duration]=AWWW_TRANS_DURATION
    [--trans-fps]=AWWW_TRANS_FPS
    [--trans-bezier]=AWWW_TRANS_BEZIER
    [--trans-angle]=AWWW_TRANS_ANGLE
    [--trans-pos]=AWWW_TRANS_POS
)

# --- VARIABLES ----------------------------------------------------------------

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
_TEMP_FILE=""

COMMAND=""
INPUT_IMAGE=""
INPUT_HEX=""
MODE_REQUEST_KIND=""

SKIP_WALL=0
SKIP_REGEN=0
CYCLE_REGEN=1

declare -A REQUESTED_SETTINGS=()

# --- LOGGING / CLEANUP ---------------------------------------------------------

log() {
    printf ':: %s\n' "$*" >&2
}

warn() {
    printf 'WARN: %s\n' "$*" >&2
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    local status=$?

    trap - EXIT

    if [[ -n "${_TEMP_FILE:-}" ]]; then
        rm -f -- "$_TEMP_FILE" ||
            printf 'WARN: Could not remove temporary file: %s\n' \
                "$_TEMP_FILE" >&2
    fi

    exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# --- GENERAL HELPERS ----------------------------------------------------------

check_deps() {
    local command
    local -a missing=()

    for command in "$@"; do
        command -v "$command" >/dev/null 2>&1 ||
            missing+=("$command")
    done

    if (( ${#missing[@]} > 0 )); then
        die "Missing required commands: ${missing[*]}"
    fi
}

ensure_dir() {
    local directory="$1"

    if [[ ( -e "$directory" || -L "$directory" ) &&
          ! -d "$directory" ]]; then
        die "Path is not a directory: $directory"
    fi

    if [[ ! -d "$directory" ]]; then
        mkdir -p -- "$directory" ||
            die "Could not create directory: $directory"
    fi
}

begin_temp() {
    local stem="$1"

    [[ -z "$_TEMP_FILE" ]] ||
        die "Internal error: temporary file already in use"

    ensure_dir "$STATE_DIR"

    _TEMP_FILE=$(mktemp "${STATE_DIR}/${stem}.XXXXXX") ||
        die "Could not create temporary file in: $STATE_DIR"
}

commit_temp() {
    local destination="$1"

    [[ -n "$_TEMP_FILE" ]] ||
        die "Internal error: no temporary file to commit"

    mv -fT -- "$_TEMP_FILE" "$destination" ||
        die "Could not replace file: $destination"

    _TEMP_FILE=""
}

discard_temp() {
    [[ -n "$_TEMP_FILE" ]] ||
        die "Internal error: no temporary file to discard"

    rm -f -- "$_TEMP_FILE" ||
        die "Could not remove temporary file: $_TEMP_FILE"

    _TEMP_FILE=""
}

# Resolve through symlinks, preserving trailing whitespace.
# Newlines are unsupported by the tracker/query formats.
#
# Usage: canonicalize_path INPUT OUTPUT_VARIABLE [-e|-m]
canonicalize_path() {
    local _input="$1"
    local _mode="${3:--e}"
    local -n _output_ref="$2"

    [[ "$_input" != *$'\n'* ]] ||
        die "Paths containing newlines are unsupported"

    if ! IFS= read -r -d '' _output_ref < <(
        realpath "$_mode" -z -- "$_input"
    ); then
        die "Could not resolve path: $_input"
    fi

    [[ "$_output_ref" != *$'\n'* ]] ||
        die "Resolved paths containing newlines are unsupported"
}

validate_image() {
    local image="$1"

    [[ -f "$image" ]] ||
        die "Image file does not exist: $image"

    [[ -r "$image" ]] ||
        die "Image file is not readable: $image"
}

# --- VALIDATION ---------------------------------------------------------------

is_valid_number() {
    [[ "$1" =~ ^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)$ ]]
}

is_valid_nonnegative_decimal() {
    [[ "$1" =~ ^([0-9]+(\.[0-9]*)?|\.[0-9]+)$ ]]
}

is_valid_setting() {
    local key="$1"
    local value="$2"

    case "$key" in
        THEME_MODE)
            [[ "$value" == "light" || "$value" == "dark" ]]
            ;;

        MATUGEN_TYPE)
            case "$value" in
                disable|scheme-content|scheme-expressive|scheme-fidelity|\
                scheme-fruit-salad|scheme-monochrome|scheme-neutral|\
                scheme-rainbow|scheme-tonal-spot|scheme-vibrant|scheme-smart)
                    return 0
                    ;;
                *)
                    return 1
                    ;;
            esac
            ;;

        MATUGEN_CONTRAST)
            [[ "$value" == "disable" ||
               "$value" =~ ^[+-]?(0+(\.[0-9]*)?|0*1(\.0*)?|\.[0-9]+)$ ]]
            ;;

        SOURCE_COLOR_INDEX)
            # Matugen 4.2.0 actually accepts 0–3.
            # Its help advertises 0–4, but its CLI parser rejects 4.
            # Individual images may provide fewer colors; generation handles
            # that separately with a narrowly matched index-0 fallback.
            [[ "$value" =~ ^0*[0-3]$ ]]
            ;;

        BASE16_BACKEND)
            [[ "$value" == "disable" || "$value" == "wal" ]]
            ;;

        AWWW_TRANS_TYPE)
            case "$value" in
                disable|none|simple|fade|left|right|top|bottom|\
                wipe|wave|grow|center|any|outer|random)
                    return 0
                    ;;
                *)
                    return 1
                    ;;
            esac
            ;;

        AWWW_TRANS_DURATION)
            [[ "$value" == "disable" ]] ||
                is_valid_nonnegative_decimal "$value"
            ;;

        AWWW_TRANS_FPS)
            if [[ "$value" == "disable" ]]; then
                return 0
            fi

            [[ "$value" =~ ^[0-9]+$ && "$value" =~ [1-9] ]]
            ;;

        AWWW_TRANS_ANGLE)
            [[ "$value" == "disable" ]] ||
                is_valid_number "$value"
            ;;

        AWWW_TRANS_BEZIER)
            [[ "$value" == "disable" ]] && return 0

            [[ "$value" =~ ^[+-]?[0-9]*\.?[0-9]+,[[:space:]]*[+-]?[0-9]*\.?[0-9]+,[[:space:]]*[+-]?[0-9]*\.?[0-9]+,[[:space:]]*[+-]?[0-9]*\.?[0-9]+$ ]]
            ;;

        AWWW_TRANS_POS)
            case "$value" in
                disable|center|top|left|right|bottom|\
                top-left|top-right|bottom-left|bottom-right)
                    return 0
                    ;;
            esac

            [[ "$value" =~ ^[+-]?[0-9]*\.?[0-9]+,[[:space:]]*[+-]?[0-9]*\.?[0-9]+$ ]]
            ;;

        *)
            return 1
            ;;
    esac
}

normalize_setting() {
    local key="$1"
    local value="$2"

    case "$key" in
        SOURCE_COLOR_INDEX|AWWW_TRANS_FPS)
            if [[ "$value" != "disable" ]]; then
                # Strip leading zeros without arithmetic conversion.
                value="${value#"${value%%[!0]*}"}"
                value="${value:-0}"
            fi
            ;;

        AWWW_TRANS_BEZIER|AWWW_TRANS_POS)
            value="${value//[[:space:]]/}"
            ;;
    esac

    printf '%s' "$value"
}

# --- STATE MANAGEMENT ---------------------------------------------------------

read_state() {
    local key value normalized line
    local -A found=()

    STATE_NEEDS_REWRITE=0

    for key in "${STATE_KEYS[@]}"; do
        printf -v "$key" '%s' "${DEFAULTS[$key]}"
    done

    if [[ ! -e "$STATE_FILE" && ! -L "$STATE_FILE" ]]; then
        STATE_NEEDS_REWRITE=1
        return 0
    fi

    [[ -f "$STATE_FILE" && -r "$STATE_FILE" ]] ||
        die "Cannot read state file: $STATE_FILE"

    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"

        [[ -z "$line" || "$line" == \#* ]] && continue
        [[ "$line" == *=* ]] || continue

        key="${line%%=*}"
        value="${line#*=}"

        key="${key%"${key##*[![:space:]]}"}"
        value="${value#"${value%%[![:space:]]*}"}"

        [[ -n "$key" ]] || continue
        [[ -v DEFAULTS["$key"] ]] || continue

        if [[ ${#value} -ge 2 ]]; then
            if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]] ||
                [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]
            then
                value="${value:1:-1}"
            fi
        fi

        if [[ -n "${found[$key]:-}" ]]; then
            STATE_NEEDS_REWRITE=1
        fi

        # Last assignment wins on read; duplicate assignments are removed
        # on the next write.
        printf -v "$key" '%s' "$value"
        found["$key"]=1
    done < "$STATE_FILE"

    for key in "${STATE_KEYS[@]}"; do
        if [[ -z "${found[$key]:-}" ]]; then
            STATE_NEEDS_REWRITE=1
        fi

        value="${!key}"

        if ! is_valid_setting "$key" "$value"; then
            warn "Invalid ${key}; resetting to ${DEFAULTS[$key]}."
            printf -v "$key" '%s' "${DEFAULTS[$key]}"
            STATE_NEEDS_REWRITE=1
            continue
        fi

        normalized=$(normalize_setting "$key" "$value")

        if [[ "$normalized" != "$value" ]]; then
            printf -v "$key" '%s' "$normalized"
            STATE_NEEDS_REWRITE=1
        fi
    done
}

write_public_state() {
    local expected existing=""

    case "$THEME_MODE" in
        dark) expected="true" ;;
        light) expected="false" ;;
        *) die "Internal error: invalid theme mode: $THEME_MODE" ;;
    esac

    # Avoid unnecessary replacement on ordinary cycling/get operations.
    if [[ -f "$PUBLIC_STATE_FILE" && -r "$PUBLIC_STATE_FILE" ]]; then
        existing=$(<"$PUBLIC_STATE_FILE")
        [[ "$existing" != "$expected" ]] || return 0
    fi

    begin_temp state

    printf '%s\n' "$expected" > "$_TEMP_FILE" ||
        die "Could not write public state"

    commit_temp "$PUBLIC_STATE_FILE"
}

write_state() {
    local line eval_line key
    local -A written=()

    begin_temp state.conf

    if [[ -s "$STATE_FILE" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            eval_line="${line#"${line%%[![:space:]]*}"}"

            if [[ -z "$eval_line" ||
                  "$eval_line" == \#* ||
                  "$eval_line" != *=* ]]; then
                printf '%s\n' "$line" ||
                    die "Could not write state temporary file"
                continue
            fi

            key="${eval_line%%=*}"
            key="${key%"${key##*[![:space:]]}"}"

            if [[ -n "$key" ]] && [[ -v DEFAULTS["$key"] ]]; then
                if [[ -z "${written[$key]:-}" ]]; then
                    printf '%s="%s"\n' "$key" "${!key}" ||
                        die "Could not write state temporary file"
                    written["$key"]=1
                fi
            else
                printf '%s\n' "$line" ||
                    die "Could not write state temporary file"
            fi
        done < "$STATE_FILE" > "$_TEMP_FILE"
    else
        printf '# Dusky Theme State File\n' > "$_TEMP_FILE" ||
            die "Could not write state temporary file"
    fi

    for key in "${STATE_KEYS[@]}"; do
        if [[ -z "${written[$key]:-}" ]]; then
            printf '%s="%s"\n' "$key" "${!key}" >> "$_TEMP_FILE" ||
                die "Could not append to state temporary file"
        fi
    done

    commit_temp "$STATE_FILE"
    write_public_state

    STATE_NEEDS_REWRITE=0
}

init_state() {
    ensure_dir "$STATE_DIR"
    read_state

    if (( STATE_NEEDS_REWRITE )); then
        write_state
    else
        write_public_state
    fi
}

# --- DAEMON / QUERY ------------------------------------------------------------

query_awww() {
    timeout -k 1s "${AWWW_QUERY_TIMEOUT_SEC}s" awww query
}

# Return:
#   0 = an image was found
#   1 = color entries but no image
#   2 = unrecognized query output
#
# Global palette generation uses the first reported image if monitors differ.
parse_awww_image() {
    local query="$1"
    local -n _parsed_ref="$2"
    local line
    local -i saw_color=0

    _parsed_ref=""

    while IFS= read -r line; do
        if [[ "$line" == *"currently displaying: image: "* ]]; then
            _parsed_ref="${line#*"currently displaying: image: "}"

            [[ -n "$_parsed_ref" && "$_parsed_ref" == /* ]] ||
                return 2

            return 0
        elif [[ "$line" == *"currently displaying: color: "* ]]; then
            saw_color=1
        fi
    done <<< "$query"

    if (( saw_color )); then
        return 1
    fi

    return 2
}

ensure_awww_running() {
    local daemon_pid
    local -i deadline

    check_deps awww timeout

    if query_awww >/dev/null 2>&1; then
        return 0
    fi

    check_deps awww-daemon
    log "Starting awww-daemon..."

    # Process existence alone does not establish daemon readiness.
    awww-daemon --format argb </dev/null >/dev/null 2>&1 &
    daemon_pid=$!

    disown "$daemon_pid" 2>/dev/null || true

    deadline=$(( SECONDS + DAEMON_START_TIMEOUT_SEC ))

    while (( SECONDS < deadline )); do
        if query_awww >/dev/null 2>&1; then
            return 0
        fi

        sleep "$DAEMON_POLL_INTERVAL"
    done

    die "awww-daemon did not become ready; try launching 'awww-daemon --format argb' manually to inspect its error"
}

# --- CURRENT-IMAGE RECORD ------------------------------------------------------

read_current_record() {
    local -n _reported_ref="$1"
    local -n _source_ref="$2"
    local -a fields=()

    _reported_ref=""
    _source_ref=""

    if [[ ! -e "$CURRENT_IMAGE_FILE" && ! -L "$CURRENT_IMAGE_FILE" ]]; then
        return 1
    fi

    [[ -f "$CURRENT_IMAGE_FILE" && -r "$CURRENT_IMAGE_FILE" ]] ||
        die "Cannot read current-image record: $CURRENT_IMAGE_FILE"

    mapfile -d '' -t fields < "$CURRENT_IMAGE_FILE" ||
        die "Could not read current-image record"

    (( ${#fields[@]} == 2 )) ||
        die "Malformed current-image record: $CURRENT_IMAGE_FILE"

    if [[ "${fields[0]}" != /* ||
          "${fields[1]}" != /* ||
          "${fields[0]}" == *$'\n'* ||
          "${fields[1]}" == *$'\n'* ]]; then
        die "Invalid paths in current-image record: $CURRENT_IMAGE_FILE"
    fi

    _reported_ref="${fields[0]}"
    _source_ref="${fields[1]}"
}

write_current_record() {
    local reported="$1"
    local source="$2"

    begin_temp current_image

    printf '%s\0%s\0' "$reported" "$source" > "$_TEMP_FILE" ||
        die "Could not write current-image record"

    commit_temp "$CURRENT_IMAGE_FILE"
}

# --- DIRECTORY MANAGEMENT -----------------------------------------------------

# Map an existing image's canonical pathname through the planned swap.
remap_path_for_mode() {
    local path="$1"
    local target_mode="$2"
    local source_dir stash_dir
    local active_root source_root stash_root

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
            die "Internal error: invalid mode: $target_mode"
            ;;
    esac

    if [[ -d "$source_dir" ]]; then
        canonicalize_path "$ACTIVE_THEME_DIR" active_root -m
        canonicalize_path "$source_dir" source_root -m
        canonicalize_path "$stash_dir" stash_root -m

        if [[ "$path" == "$active_root/"* ]]; then
            path="${stash_root}/${path#"$active_root"/}"
        elif [[ "$path" == "$source_root/"* ]]; then
            path="${active_root}/${path#"$source_root"/}"
        fi
    fi

    printf '%s\n' "$path"
}

move_directories() {
    local target_mode="$1"
    local -i mode_changed="${2:-0}"

    local source_dir stash_dir directory
    local reported="" source="" queried="" output="" mapped=""
    local mv_help source_device destination_device
    local -i had_active=0
    local -i record_known=0
    local -i query_status=0

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
            die "Internal error: invalid mode: $target_mode"
            ;;
    esac

    ensure_dir "$WALLPAPER_ROOT"

    for directory in "$source_dir" "$stash_dir" "$ACTIVE_THEME_DIR"; do
        [[ ! -L "$directory" ]] ||
            die "Physical theme slots must not be symlinks: $directory"

        if [[ -e "$directory" && ! -d "$directory" ]]; then
            die "Theme path is not a directory: $directory"
        fi
    done

    if [[ ! -d "$source_dir" ]]; then
        if (( mode_changed )) && [[ -d "$ACTIVE_THEME_DIR" ]]; then
            die "Cannot switch mode: incoming directory is missing: $source_dir"
        fi

        if [[ ! -d "$ACTIVE_THEME_DIR" ]]; then
            warn "Neither stored '$target_mode' nor active_theme exists."
        fi

        return 0
    fi

    if [[ -d "$ACTIVE_THEME_DIR" && -e "$stash_dir" ]]; then
        die "Ambiguous theme layout: '$stash_dir' and active_theme both exist while activating '$source_dir'"
    fi

    check_deps stat

    # Do not allow mv to silently copy/delete across a filesystem boundary.
    mv_help=$(LC_ALL=C mv --help) ||
        die "Could not inspect GNU mv capabilities"

    [[ "$mv_help" == *"--no-copy"* ]] ||
        die "Directory swaps require GNU mv with --no-copy support"

    source_device=$(stat -Lc '%d' -- "$source_dir") ||
        die "Could not inspect filesystem: $source_dir"
    destination_device=$(stat -Lc '%d' -- "$WALLPAPER_ROOT") ||
        die "Could not inspect filesystem: $WALLPAPER_ROOT"

    [[ "$source_device" == "$destination_device" ]] ||
        die "Cross-filesystem theme swap is unsupported: $source_dir"

    if [[ -d "$ACTIVE_THEME_DIR" ]]; then
        source_device=$(stat -Lc '%d' -- "$ACTIVE_THEME_DIR") ||
            die "Could not inspect active-theme filesystem"
        destination_device=$(stat -Lc '%d' -- "$BASE_PICTURES") ||
            die "Could not inspect Pictures filesystem"

        [[ "$source_device" == "$destination_device" ]] ||
            die "Cross-filesystem active-theme swap is unsupported"
    fi

    if read_current_record reported source; then
        record_known=1
    fi

    # Capture image identity before active_theme can point to another image
    # with the same filename. Do not start a daemon solely to move directories.
    if command -v awww >/dev/null 2>&1 &&
        output=$(query_awww 2>/dev/null)
    then
        if parse_awww_image "$output" queried; then
            if [[ "$queried" != "$reported" || -z "$source" ]]; then
                canonicalize_path "$queried" source -m
            fi

            reported="$queried"
            record_known=1
        else
            query_status=$?

            if (( query_status == 1 )); then
                reported=""
                source=""
                record_known=0

                rm -f -- "$CURRENT_IMAGE_FILE" ||
                    die "Could not clear stale current-image record"
            else
                die "Could not parse awww query before directory swap: $output"
            fi
        fi
    fi

    if (( record_known )); then
        mapped=$(remap_path_for_mode "$source" "$target_mode")
    fi

    log "Reconciling directories for mode: $target_mode"

    if [[ -d "$ACTIVE_THEME_DIR" ]]; then
        mv --no-copy -T -- "$ACTIVE_THEME_DIR" "$stash_dir" ||
            die "Could not stash active theme in: $stash_dir"

        had_active=1
    fi

    if ! mv --no-copy -T -- "$source_dir" "$ACTIVE_THEME_DIR"; then
        if (( had_active )); then
            if [[ ! -e "$ACTIVE_THEME_DIR" && ! -L "$ACTIVE_THEME_DIR" ]]; then
                if ! mv --no-copy -T -- "$stash_dir" "$ACTIVE_THEME_DIR"; then
                    warn "Rollback failed; inspect '$stash_dir' and '$ACTIVE_THEME_DIR'."
                fi
            else
                warn "Rollback blocked: '$ACTIVE_THEME_DIR' exists."
            fi
        fi

        die "Could not activate theme directory: $source_dir"
    fi

    if (( record_known )); then
        write_current_record "$reported" "$mapped"
    fi
}

# --- WALLPAPER SELECTION -------------------------------------------------------

tracker_file_for_mode() {
    case "$1" in
        light) printf '%s\n' "$TRACK_LIGHT" ;;
        dark) printf '%s\n' "$TRACK_DARK" ;;
        *) die "Internal error: invalid tracker mode: $1" ;;
    esac
}

resolve_wallpaper_id() {
    local path="$1"
    local active_root

    canonicalize_path "$ACTIVE_THEME_DIR" active_root -m

    if [[ "$path" == "$active_root/"* ]]; then
        printf '%s\n' "${path#"$active_root"/}"
    else
        # External and root-level images must not impersonate active-theme
        # images merely because they share a basename.
        printf '%s\n' "$path"
    fi
}

update_wallpaper_tracker() {
    local identifier="$1"
    local tracker

    tracker=$(tracker_file_for_mode "$THEME_MODE")

    begin_temp track

    printf '%s\n' "$identifier" > "$_TEMP_FILE" ||
        die "Could not write wallpaper tracker"

    commit_temp "$tracker"
}

load_wallpapers() {
    local root="$1"
    local recursive="$2"
    local -n _paths_ref="$3"
    local -n _ids_ref="$4"

    local canonical_root active_root path
    local -a depth_args=()
    local -a found=()

    _paths_ref=()
    _ids_ref=()

    [[ -d "$root" ]] || return 1

    check_deps find sort

    canonicalize_path "$root" canonical_root -e
    canonicalize_path "$ACTIVE_THEME_DIR" active_root -m

    [[ "$recursive" == "1" ]] || depth_args=(-maxdepth 1)

    begin_temp wallpapers

    # A real pipeline makes find/sort failures visible through pipefail.
    if ! find "$canonical_root" "${depth_args[@]}" -type f \
        \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
           -o -iname '*.webp' -o -iname '*.gif' \) \
        -print0 |
        LC_ALL=C sort -z -V > "$_TEMP_FILE"
    then
        die "Could not completely enumerate wallpapers in: $canonical_root"
    fi

    mapfile -d '' -t found < "$_TEMP_FILE" ||
        die "Could not read wallpaper list"

    discard_temp

    (( ${#found[@]} > 0 )) || return 1

    for path in "${found[@]}"; do
        [[ "$path" != *$'\n'* ]] ||
            die "Wallpaper filenames containing newlines are unsupported"

        _paths_ref+=("$path")

        if [[ "$path" == "$active_root/"* ]]; then
            _ids_ref+=("${path#"$active_root"/}")
        else
            _ids_ref+=("$path")
        fi
    done
}

select_wallpaper() {
    local strategy="$1"
    local -n _selected_ref="$2"

    local tracker last_id="" index
    local -i current=-1
    local -i selected=0
    local -i count=0
    local -i legacy_index=-1
    local -i legacy_matches=0

    local -a paths=()
    local -a identifiers=()
    local -a tracker_lines=()

    if ! load_wallpapers "$ACTIVE_THEME_DIR" 1 paths identifiers; then
        load_wallpapers "$WALLPAPER_ROOT" 0 paths identifiers ||
            return 1
    fi

    count=${#paths[@]}
    tracker=$(tracker_file_for_mode "$THEME_MODE")

    if [[ -e "$tracker" || -L "$tracker" ]]; then
        [[ -f "$tracker" && -r "$tracker" ]] ||
            die "Cannot read wallpaper tracker: $tracker"

        mapfile -t tracker_lines < "$tracker" ||
            die "Could not read wallpaper tracker: $tracker"

        if (( ${#tracker_lines[@]} == 1 )); then
            last_id="${tracker_lines[0]}"
        elif (( ${#tracker_lines[@]} > 1 )); then
            warn "Ignoring malformed tracker: $tracker"
        fi
    fi

    if [[ -n "$last_id" ]]; then
        # Exact matches anywhere in the collection beat basename fallbacks.
        for index in "${!paths[@]}"; do
            if [[ "${identifiers[$index]}" == "$last_id" ||
                  "${paths[$index]}" == "$last_id" ]]; then
                current=$index
                break
            fi
        done

        if (( current < 0 )) && [[ "$last_id" != */* ]]; then
            for index in "${!paths[@]}"; do
                if [[ "${paths[$index]##*/}" == "$last_id" ]]; then
                    legacy_index=$index
                    (( legacy_matches += 1 ))
                fi
            done

            if (( legacy_matches == 1 )); then
                current=$legacy_index
            fi
        fi
    fi

    case "$strategy" in
        next)
            if (( current >= 0 )); then
                selected=$(( (current + 1) % count ))
            else
                selected=0
            fi
            ;;
        prev)
            if (( current >= 0 )); then
                selected=$(( (current + count - 1) % count ))
            else
                selected=$(( count - 1 ))
            fi
            ;;
        random)
            selected=$(( SRANDOM % count ))
            ;;
        *)
            die "Internal error: invalid selection strategy: $strategy"
            ;;
    esac

    _selected_ref="${paths[$selected]}"
}

# --- MATUGEN ------------------------------------------------------------------

build_matugen_command() {
    local -n _command_ref="$1"

    _command_ref=(matugen "--mode=$THEME_MODE")

    if [[ "$MATUGEN_TYPE" != "disable" ]]; then
        _command_ref+=("--type=$MATUGEN_TYPE")
    fi

    # Explicit zero is different from omitting the option.
    if [[ "$MATUGEN_CONTRAST" != "disable" ]]; then
        _command_ref+=("--contrast=$MATUGEN_CONTRAST")
    fi

    if [[ "$BASE16_BACKEND" != "disable" ]]; then
        _command_ref+=("--base16-backend=$BASE16_BACKEND")
    fi
}

update_desktop_color_scheme() {
    command -v gsettings >/dev/null 2>&1 || return 0

    if ! timeout -k 1s 5s \
        gsettings set org.gnome.desktop.interface \
        color-scheme "prefer-${THEME_MODE}" \
        >/dev/null 2>&1
    then
        warn "Could not update desktop color-scheme preference."
    fi
}

generate_colors() {
    local image="$1"
    local output
    local -a command=()

    check_deps matugen
    validate_image "$image"

    log "Matugen: Mode=[$THEME_MODE] Type=[$MATUGEN_TYPE] Contrast=[$MATUGEN_CONTRAST] Index=[$SOURCE_COLOR_INDEX] Base16=[$BASE16_BACKEND]"

    build_matugen_command command
    command+=("--source-color-index=$SOURCE_COLOR_INDEX" image "$image")

    if ! output=$("${command[@]}" 2>&1); then
        # Retry only Matugen's specific unavailable-source-color diagnostic.
        # Do not retry CLI validation failures or unrelated Rust bounds panics:
        # those do not establish that changing the source index is appropriate.
        if [[ "$SOURCE_COLOR_INDEX" != "0" &&
              "$output" == *"Source color index ${SOURCE_COLOR_INDEX} is out of bounds ("* ]]; then
            warn "Source index $SOURCE_COLOR_INDEX unavailable; retrying index 0."

            build_matugen_command command
            command+=(--source-color-index=0 image "$image")

            if ! output=$("${command[@]}" 2>&1); then
                die "Matugen fallback failed: $output"
            fi

            SOURCE_COLOR_INDEX="0"
            write_state
        else
            die "Matugen generation failed: $output"
        fi
    fi

    update_desktop_color_scheme
}

apply_solid_color() {
    local hex="$1"
    local output
    local -a command=()

    check_deps matugen

    [[ "$hex" =~ ^#?[a-fA-F0-9]{6}$ ]] ||
        die "Invalid HEX color: $hex"

    [[ "$hex" == \#* ]] || hex="#${hex}"

    log "Generating theme from solid color: $hex"

    build_matugen_command command
    command+=(color hex "$hex")

    if ! output=$("${command[@]}" 2>&1); then
        die "Matugen color generation failed: $output"
    fi

    update_desktop_color_scheme
}

# --- WALLPAPER APPLICATION -----------------------------------------------------

apply_wallpaper_direct() {
    local input="$1"
    local -i regenerate="${2:-1}"

    local image identifier
    local -a command=(awww img)

    check_deps awww

    canonicalize_path "$input" image -e
    validate_image "$image"

    if (( regenerate )); then
        check_deps matugen
    fi

    identifier=$(resolve_wallpaper_id "$image")

    ensure_awww_running

    if [[ "$AWWW_TRANS_TYPE" != "disable" ]]; then
        command+=("--transition-type=$AWWW_TRANS_TYPE")
    fi

    if [[ "$AWWW_TRANS_DURATION" != "disable" ]]; then
        command+=("--transition-duration=$AWWW_TRANS_DURATION")
    fi

    if [[ "$AWWW_TRANS_FPS" != "disable" ]]; then
        command+=("--transition-fps=$AWWW_TRANS_FPS")
    fi

    if [[ "$AWWW_TRANS_ANGLE" != "disable" ]]; then
        command+=("--transition-angle=$AWWW_TRANS_ANGLE")
    fi

    if [[ "$AWWW_TRANS_POS" != "disable" ]]; then
        command+=("--transition-pos=$AWWW_TRANS_POS")
    fi

    if [[ "$AWWW_TRANS_BEZIER" != "disable" ]]; then
        command+=("--transition-bezier=$AWWW_TRANS_BEZIER")
    fi

    command+=("$image")

    log "Applying wallpaper: ${image##*/} [Trans: $AWWW_TRANS_TYPE]"

    "${command[@]}" ||
        die "Failed to apply wallpaper with awww"

    # Record what was applied even if subsequent Matugen generation fails.
    write_current_record "$image" "$image"
    update_wallpaper_tracker "$identifier"

    if (( regenerate )); then
        generate_colors "$image"
    fi
}

apply_wallpaper_selection() {
    local strategy="$1"
    local -i regenerate="${2:-1}"
    local selected_image

    select_wallpaper "$strategy" selected_image ||
        die "No supported wallpapers found in '$ACTIVE_THEME_DIR' or '$WALLPAPER_ROOT'"

    apply_wallpaper_direct "$selected_image" "$regenerate"
}

cycle_command() {
    local strategy="$1"
    local -i regenerate="${2:-1}"

    check_deps awww find sort

    if (( regenerate )); then
        check_deps matugen
    fi

    move_directories "$THEME_MODE"
    apply_wallpaper_selection "$strategy" "$regenerate"
}

regenerate_current() {
    local -i allow_fallback="${1:-1}"

    local output current="" resolved=""
    local recorded_query="" recorded_source=""
    local -i parse_status=0

    check_deps awww matugen

    if (( allow_fallback )); then
        ensure_awww_running
    fi

    if ! output=$(query_awww 2>&1); then
        die "awww query failed or timed out: $output"
    fi

    if parse_awww_image "$output" current; then
        resolved="$current"

        if read_current_record recorded_query recorded_source &&
            [[ "$recorded_query" == "$current" ]]
        then
            resolved="$recorded_source"
        fi

        if [[ -f "$resolved" ]]; then
            log "Current wallpaper source: $resolved"
            generate_colors "$resolved"
            return 0
        fi

        if (( ! allow_fallback )); then
            die "Current image source is missing; --no-wall forbids a fallback wallpaper: $resolved"
        fi

        warn "Current wallpaper source is missing: $resolved"
    else
        parse_status=$?

        if (( parse_status != 1 )); then
            die "Could not parse awww query output: $output"
        fi

        if (( ! allow_fallback )); then
            die "awww is displaying a color; --no-wall forbids a fallback wallpaper"
        fi

        log "awww is displaying a color; selecting a random wallpaper."
    fi

    cycle_command random 1
}

# --- COMMAND IMPLEMENTATIONS --------------------------------------------------

cmd_get() {
    cat -- "$STATE_FILE"
    printf '\n# Public State (%s):\n' "$PUBLIC_STATE_FILE"
    cat -- "$PUBLIC_STATE_FILE"
}

cmd_set() {
    local previous_mode="$THEME_MODE"
    local previous_type="$MATUGEN_TYPE"
    local previous_contrast="$MATUGEN_CONTRAST"
    local previous_index="$SOURCE_COLOR_INDEX"
    local previous_base16="$BASE16_BACKEND"

    local key image="" mapped_image=""

    local -i mode_changed=0
    local -i palette_changed=0
    local -i same_mode_requested=0
    local -i want_wall=0
    local -i want_regen=0

    for key in "${STATE_KEYS[@]}"; do
        if [[ -v REQUESTED_SETTINGS["$key"] ]]; then
            printf -v "$key" '%s' "${REQUESTED_SETTINGS[$key]}"
        fi
    done

    if [[ "$THEME_MODE" != "$previous_mode" ]]; then
        mode_changed=1
    fi

    if [[ "$MATUGEN_TYPE" != "$previous_type" ||
          "$MATUGEN_CONTRAST" != "$previous_contrast" ||
          "$SOURCE_COLOR_INDEX" != "$previous_index" ||
          "$BASE16_BACKEND" != "$previous_base16" ]]; then
        palette_changed=1
    fi

    if [[ "$MODE_REQUEST_KIND" == "explicit" ]] &&
        (( ! mode_changed ))
    then
        same_mode_requested=1
    fi

    # Resolve the requested file before directory mutation changes its meaning.
    if [[ -n "$INPUT_IMAGE" ]]; then
        canonicalize_path "$INPUT_IMAGE" image -e
        validate_image "$image"

        want_wall=$(( ! SKIP_WALL ))
        want_regen=$(( ! SKIP_REGEN ))
    elif (( ! SKIP_WALL && (mode_changed || same_mode_requested) )); then
        want_wall=1
        want_regen=$(( ! SKIP_REGEN ))
    elif (( ! SKIP_REGEN &&
            (mode_changed || same_mode_requested || palette_changed) )); then
        want_regen=1
    fi

    if (( want_wall )); then
        check_deps awww

        if [[ -z "$image" ]]; then
            check_deps find sort
        fi
    fi

    if (( want_regen )); then
        check_deps matugen

        if [[ -z "$image" ]] && (( ! want_wall )); then
            check_deps awww
        fi
    fi

    if (( mode_changed || same_mode_requested )); then
        if [[ -n "$image" ]]; then
            mapped_image=$(remap_path_for_mode "$image" "$THEME_MODE")
        fi

        move_directories "$THEME_MODE" "$mode_changed"

        if [[ -n "$mapped_image" ]]; then
            image="$mapped_image"
        fi
    fi

    # Desired configuration is saved before external application.
    write_state

    if (( want_wall )); then
        if [[ -n "$image" ]]; then
            apply_wallpaper_direct "$image" "$want_regen"
        else
            apply_wallpaper_selection next "$want_regen"
        fi
    elif (( want_regen )); then
        if [[ -n "$image" ]]; then
            # Explicit image + --no-wall = palette generation only.
            generate_colors "$image"
        else
            regenerate_current "$(( ! SKIP_WALL ))"
        fi
    fi
}

# --- CLI ----------------------------------------------------------------------

usage() {
    cat <<'EOF'
Usage: theme_ctl [COMMAND] [OPTIONS]

Commands:
  set [image_path]
      Save settings and apply relevant changes.

      --mode <light|dark>
      --type <scheme-*|disable>
      --contrast <decimal[-1..1]|disable>
      --index <0..3>            Matugen source color index
      --base16 <wal|disable>

      --trans-type <type|disable>
      --trans-duration <seconds|disable>
      --trans-fps <positive-integer|disable>
      --trans-bezier <curve|disable>
      --trans-angle <degrees|disable>
      --trans-pos <position|disable>

      --defaults               Reset all settings to defaults
      --no-wall                Do not change the displayed wallpaper
      --no-regen               Do not execute Matugen
      --help                   Show help

  next [--no-regen]             Select the next wallpaper
  prev [--no-regen]             Select the previous wallpaper
  previous [--no-regen]         Alias of prev
  random [--no-regen]           Select a random wallpaper

  refresh                      Regenerate from the current wallpaper
  apply                        Alias of refresh
  color <hex>                  Generate a theme from a six-digit hex color
  get                          Show saved configuration and public state

  -h, --help, help              Show help

Notes:
  - No locking or request serialization is used.
  - Avoid overlapping mode switches and Matugen generation.
  - For rapid wallpaper-only cycling, use next/prev/random --no-regen.
  - An image with --no-wall supplies colors without being displayed.
  - --no-wall still permits mode-directory reconciliation.
  - "disable" omits an option; backend defaults/configuration still apply.
  - Animation-only changes are saved for subsequent wallpaper operations.
  - Explicitly requesting the current mode advances the wallpaper unless
    --no-wall is supplied.
  - refresh may select a random wallpaper if the current source is missing
    or awww is displaying only solid colors.
  - A multi-monitor refresh uses the first reported image.
  - Wallpaper filenames containing newlines are unsupported.
  - Use an absolute path or ./filename for filenames starting with "-".

Examples:
  theme_ctl set --mode dark --type scheme-smart
  theme_ctl set --trans-type wave --trans-duration 2.5
  theme_ctl set /path/to/wallpaper.jpg --mode dark
  theme_ctl set /path/to/source.png --no-wall
  theme_ctl next --no-regen
  theme_ctl prev
  theme_ctl random
  theme_ctl refresh
  theme_ctl color "#FF0000"
EOF
}

parse_cli() {
    local requested="${1:-}"
    local option key value

    if [[ -z "$requested" ]]; then
        usage
        exit 1
    fi

    shift

    case "$requested" in
        -h|--help|help)
            (( $# == 0 )) ||
                die "Help does not accept additional arguments"
            COMMAND="help"
            ;;

        set)
            COMMAND="set"

            while (( $# > 0 )); do
                option="$1"

                case "$option" in
                    --mode|--type|--contrast|--index|--base16|\
                    --trans-type|--trans-duration|--trans-fps|\
                    --trans-bezier|--trans-angle|--trans-pos)
                        (( $# >= 2 )) ||
                            die "$option requires a value"

                        [[ -n "$2" ]] ||
                            die "$option requires a nonempty value"

                        key="${OPTION_KEYS[$option]}"
                        value="$2"

                        is_valid_setting "$key" "$value" ||
                            die "Invalid value for $option: $value"

                        value=$(normalize_setting "$key" "$value")
                        REQUESTED_SETTINGS["$key"]="$value"

                        if [[ "$key" == "THEME_MODE" ]]; then
                            MODE_REQUEST_KIND="explicit"
                        fi

                        shift 2
                        ;;

                    --defaults)
                        for key in "${STATE_KEYS[@]}"; do
                            REQUESTED_SETTINGS["$key"]="${DEFAULTS[$key]}"
                        done
                        MODE_REQUEST_KIND="defaults"
                        shift
                        ;;

                    --no-wall)
                        SKIP_WALL=1
                        shift
                        ;;

                    --no-regen)
                        SKIP_REGEN=1
                        shift
                        ;;

                    --help)
                        COMMAND="help"
                        return 0
                        ;;

                    -*)
                        die "Unknown option: $option"
                        ;;

                    *)
                        [[ -z "$INPUT_IMAGE" ]] ||
                            die "Unexpected additional argument: $option"

                        [[ -n "$option" ]] ||
                            die "Image path must not be empty"

                        [[ "$option" != *$'\n'* ]] ||
                            die "Image paths containing newlines are unsupported"

                        INPUT_IMAGE="$option"
                        shift
                        ;;
                esac
            done
            ;;

        next|prev|previous|random)
            case "$requested" in
                previous) COMMAND="prev" ;;
                *) COMMAND="$requested" ;;
            esac

            for option in "$@"; do
                case "$option" in
                    --no-regen)
                        CYCLE_REGEN=0
                        ;;
                    --help)
                        COMMAND="help"
                        return 0
                        ;;
                    *)
                        die "Unknown argument for $requested: $option"
                        ;;
                esac
            done
            ;;

        refresh|apply)
            (( $# == 0 )) ||
                die "$requested does not accept arguments"
            COMMAND="refresh"
            ;;

        color)
            (( $# == 1 )) ||
                die 'color requires one hex value, e.g. FF0000 or "#FF0000"'

            [[ "$1" =~ ^#?[a-fA-F0-9]{6}$ ]] ||
                die "Invalid HEX color: $1"

            COMMAND="color"
            INPUT_HEX="$1"
            ;;

        get)
            (( $# == 0 )) ||
                die "get does not accept arguments"
            COMMAND="get"
            ;;

        *)
            die "Unknown command: $requested"
            ;;
    esac
}

# --- MAIN ---------------------------------------------------------------------

parse_cli "$@"

if [[ "$COMMAND" == "help" ]]; then
    usage
    exit 0
fi

[[ "$HOME" != *$'\n'* ]] ||
    die "HOME paths containing newlines are unsupported"

check_deps mkdir mktemp mv rm realpath timeout cat

# Catch invalid explicit images before initializing or rewriting state.
if [[ -n "$INPUT_IMAGE" ]]; then
    canonicalize_path "$INPUT_IMAGE" INPUT_IMAGE -e
    validate_image "$INPUT_IMAGE"
fi

# Check command-specific dependencies before state initialization.
case "$COMMAND" in
    next|prev|random)
        check_deps awww find sort
        if (( CYCLE_REGEN )); then
            check_deps matugen
        fi
        ;;
    refresh)
        check_deps awww matugen
        ;;
    color)
        check_deps matugen
        ;;
esac

init_state

case "$COMMAND" in
    set)
        cmd_set
        ;;
    next|prev|random)
        cycle_command "$COMMAND" "$CYCLE_REGEN"
        ;;
    refresh)
        regenerate_current
        ;;
    color)
        apply_solid_color "$INPUT_HEX"
        ;;
    get)
        cmd_get
        ;;
    *)
        die "Internal error: invalid parsed command: $COMMAND"
        ;;
esac
