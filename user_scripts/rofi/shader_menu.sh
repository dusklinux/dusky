#!/usr/bin/env bash
#
# Hyprland shader picker with Up/Down live preview.
# Requires: rofi, hyprctl, flock, python3.
#
# Applies use the supplied hl.config Lua interface.
# A successful option readback does not prove GPU shader compilation.
#

set -euo pipefail

declare SHADER_DIR="$HOME/.config/hypr/shaders"
declare -r MEMORY_FILE="$HOME/.config/dusky/settings/dusky_shader/rofi_shader_memory"

declare -ra ROFI_CMD=(
    rofi
    -dmenu
    -i
    -no-markup-rows
    -no-custom
    -no-sort
    -theme-str 'window { width: 400px; }'
)

# Empty path represents "Turn Off"; real shader entries retain full paths.
declare -a SHADERS=("")
declare -a MENU_LINES=("Turn Off")

declare ORIGINAL_SHADER=""
declare LIVE_SHADER=""
declare SEARCH_QUERY=""
declare MENU_TEXT=""
declare LOCK_FD=""

declare -i CURRENT_IDX=0
declare -i ACTIVE_IDX=-1
declare -i MATCH_IDX=-1
declare -i RESTORE_NEEDED=0

err() {
    printf 'Error: %s\n' "$*" >&2
}

check_dependencies() {
    local cmd
    local -a missing=()

    for cmd in rofi hyprctl flock python3; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done

    if ((${#missing[@]})); then
        err "Missing required commands: ${missing[*]}"
        return 1
    fi
}

# Set LIVE_SHADER only after a successful query and valid JSON parsing.
# A NUL delimiter preserves paths ending in newline characters.
read_live_shader() {
    local json
    local -a values=()

    json=$(hyprctl getoption decoration:screen_shader -j) || return 1

    mapfile -d '' -t values < <(
        python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
    if not isinstance(data, dict) or not isinstance(data.get("str"), str):
        raise ValueError("expected a string-valued str member")
    value = data["str"]
    if value == "[[EMPTY]]":
        value = ""
except (ValueError, TypeError) as exc:
    print(f"Cannot parse screen_shader state: {exc}", file=sys.stderr)
    sys.exit(1)

sys.stdout.write(value + "\0")
' <<< "$json"
    )

    ((${#values[@]} == 1)) || return 1
    LIVE_SHADER="${values[0]}"
}

# Exact configured-path matches take priority. Filesystem aliases may
# identify the same shader when Hyprland reports another path spelling.
find_shader_index() {
    local wanted="$1"
    local i

    MATCH_IDX=-1

    for i in "${!SHADERS[@]}"; do
        if [[ "${SHADERS[i]}" == "$wanted" ]]; then
            MATCH_IDX=$i
            return 0
        fi
    done

    if [[ -n "$wanted" && -f "$wanted" ]]; then
        for i in "${!SHADERS[@]}"; do
            if [[ -n "${SHADERS[i]}" && "${SHADERS[i]}" -ef "$wanted" ]]; then
                MATCH_IDX=$i
                return 0
            fi
        done
    fi

    return 0
}

apply_shader() {
    local path="$1"
    local lua

    if [[ -n "$path" && ! -f "$path" ]]; then
        err "Shader file no longer exists: $path"
        return 1
    fi

    # Lua decimal byte escapes preserve the filesystem path without
    # relying on JSON escapes being valid Lua escapes.
    lua=$(python3 - "$path" <<'PY'
import os
import sys

encoded = "".join(f"\\{byte:03d}" for byte in os.fsencode(sys.argv[1]))
print('hl.config({ decoration = { screen_shader = "' + encoded + '" } })')
PY
    ) || return 1

    hyprctl eval "$lua" >/dev/null || return 1
    read_live_shader || return 1

    if [[ "$LIVE_SHADER" != "$path" ]]; then
        err "screen_shader readback does not match the requested value."
        return 1
    fi

    return 0
}

# Output the remembered value followed by NUL.
# New values are JSON strings; old plain stem values remain readable.
read_memory() {
    python3 - "$MEMORY_FILE" <<'PY'
import json
import sys
from pathlib import Path

try:
    lines = Path(sys.argv[1]).read_text().splitlines()
except OSError:
    sys.exit(0)

raw = None
for line in lines:
    if line.startswith("shader_menu="):
        raw = line[len("shader_menu="):]

if raw is None:
    sys.exit(0)

try:
    value = json.loads(raw)
except ValueError:
    value = raw

if not isinstance(value, str):
    value = raw

if raw == "off":
    value = ""

sys.stdout.write(value + "\0")
PY
}

write_memory() {
    local path="$1"

    mkdir -p -- "${MEMORY_FILE%/*}" || return 1

    python3 - "$MEMORY_FILE" "$path" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])

try:
    lines = path.read_text().splitlines()
except FileNotFoundError:
    lines = []

lines = [line for line in lines if not line.startswith("shader_menu=")]
lines.append("shader_menu=" + json.dumps(sys.argv[2], ensure_ascii=True))
path.write_text("\n".join(lines) + "\n")
PY
}

choose_initial_row() {
    local remembered
    local name
    local i
    local -i legacy_idx=-1
    local -i legacy_count=0
    local -a values=()

    find_shader_index "$ORIGINAL_SHADER"
    ACTIVE_IDX=$MATCH_IDX

    if ((ACTIVE_IDX >= 0)); then
        CURRENT_IDX=$ACTIVE_IDX
        return 0
    fi

    CURRENT_IDX=0
    mapfile -d '' -t values < <(read_memory)
    ((${#values[@]} == 1)) || return 0
    remembered="${values[0]}"

    find_shader_index "$remembered"
    if ((MATCH_IDX >= 0)); then
        CURRENT_IDX=$MATCH_IDX
        return 0
    fi

    # Migrate a legacy basename-without-extension only if unambiguous.
    for i in "${!SHADERS[@]}"; do
        [[ -n "${SHADERS[i]}" ]] || continue
        name="${SHADERS[i]##*/}"

        if [[ "${name%.*}" == "$remembered" ]]; then
            legacy_idx=$i
            legacy_count=$((legacy_count + 1))
        fi
    done

    if ((legacy_count == 1)); then
        CURRENT_IDX=$legacy_idx
    fi

    return 0
}

build_menu() {
    local file
    local label

    shopt -s nullglob dotglob

    for file in "$SHADER_DIR/"*.glsl "$SHADER_DIR/"*.frag; do
        [[ -f "$file" ]] || continue

        SHADERS+=("$file")
        label="${file##*/}"

        # Keep every filename on one rofi row. Escape backslash first
        # so literal "\n" and a real newline remain distinguishable.
        label=${label//\\/'\\'}
        label=${label//$'\n'/'\n'}
        label=${label//$'\r'/'\r'}
        label=${label//$'\t'/'\t'}

        MENU_LINES+=("$label")
    done

    # A here-string adds exactly one final newline when invoking rofi.
    printf -v MENU_TEXT '%s\n' "${MENU_LINES[@]}"
    MENU_TEXT="${MENU_TEXT%$'\n'}"
}

notify_applied() {
    local path="$1"
    local message="Off"

    if [[ -n "$path" ]]; then
        message="${path##*/}"
    fi

    if command -v notify-send >/dev/null 2>&1; then
        notify-send -i video-display \
            "Hyprshade" "Applied: $message" >/dev/null 2>&1 || true
    fi
}

cleanup() {
    local status=$?

    # Cleanup keeps the existing session lock; it never reacquires it.
    trap '' INT TERM HUP

    if ((RESTORE_NEEDED)); then
        if ! apply_shader "$ORIGINAL_SHADER"; then
            err "Failed to restore the original screen shader."
            if ((status == 0)); then
                status=1
            fi
        fi
    fi

    # Process exit releases the session lock descriptor.
    exit "$status"
}

valid_index() {
    [[ "$1" =~ ^[0-9]+$ ]] || return 1
    (($1 < ${#SHADERS[@]}))
}

# Build the currently visible rows for SEARCH_QUERY.
# view_idx[k] is the index into SHADERS/MENU_LINES for visible row k.
#
# Filtering is done here instead of via rofi's -filter flag because
# rofi 2.x parses a filter value that is exactly one color keyword
# (red, blue, green, ...) as a Color option and aborts with
# "Option: filter needs to be set with a string not a Color."
# Feeding rofi only the matching rows avoids -filter (and -dump) entirely.
build_view() {
    local -n _idx=$1
    local -n _lines=$2
    local row label hay tok
    local -a tokens=()

    _idx=()
    _lines=()

    if [[ -z "${SEARCH_QUERY//[[:space:]]/}" ]]; then
        for row in "${!SHADERS[@]}"; do
            _idx+=("$row")
            _lines+=("${MENU_LINES[row]}")
        done
        return 0
    fi

    read -ra tokens <<< "${SEARCH_QUERY,,}" || true

    for row in "${!SHADERS[@]}"; do
        label="${MENU_LINES[row]}"
        hay="${label,,}"
        for tok in "${tokens[@]}"; do
            [[ "$hay" == *"$tok"* ]] || continue 2
        done
        _idx+=("$row")
        _lines+=("$label")
    done
}

main_loop() {
    local raw_output
    local selection
    local target
    local query_display
    local view_text
    local i
    local -i exit_code
    local -i view_pos
    local -i view_count
    local -i sel_row
    local -i act_row
    local -a flags=()
    local -a view_idx=()
    local -a view_lines=()

    while true; do
        build_view view_idx view_lines
        view_count=${#view_idx[@]}

        if ((view_count > 0)); then
            printf -v view_text '%s\n' "${view_lines[@]}"
            view_text="${view_text%$'\n'}"
        else
            view_text=""
        fi

        # Cursor follows CURRENT_IDX when it is visible, else first row.
        sel_row=0
        for i in "${!view_idx[@]}"; do
            if ((view_idx[i] == CURRENT_IDX)); then
                sel_row=$i
                break
            fi
        done

        if [[ -n "$SEARCH_QUERY" ]]; then
            query_display="Shader [$SEARCH_QUERY]"
        else
            query_display="Shader Preview"
        fi

        flags=(
            -p "$query_display"
            -format 'i|f'
            -selected-row "$sel_row"
            -kb-custom-1 "Down"
            -kb-custom-2 "Up"
            -kb-row-down ""
            -kb-row-up ""
        )

        act_row=-1
        if ((ACTIVE_IDX >= 0)); then
            for i in "${!view_idx[@]}"; do
                if ((view_idx[i] == ACTIVE_IDX)); then
                    act_row=$i
                    break
                fi
            done
        fi

        if ((act_row >= 0)); then
            flags+=(-a "$act_row")
        fi

        # No producer pipeline: capture rofi's status directly.
        if raw_output=$(
            "${ROFI_CMD[@]}" "${flags[@]}" <<< "$view_text"
        ); then
            exit_code=0
        else
            exit_code=$?
        fi

        selection="${raw_output%%|*}"

        if [[ "$raw_output" == *"|"* ]]; then
            SEARCH_QUERY="${raw_output#*|}"
            SEARCH_QUERY="${SEARCH_QUERY//$'\n'/}"
        fi

        case "$exit_code" in
            0)
                # view positions map back through view_idx; rofi only
                # ever sees the pre-filtered rows, so no -dump lookup.
                if [[ "$selection" =~ ^[0-9]+$ ]] && ((selection >= 0 && selection < view_count)); then
                    target="${SHADERS[view_idx[selection]]}"
                else
                    # e.g. Enter on an empty view: keep browsing.
                    continue
                fi

                # Even a partially failed apply requires rollback.
                RESTORE_NEEDED=1
                if ! apply_shader "$target"; then
                    err "Failed to apply the selected shader."
                    exit 1
                fi

                RESTORE_NEEDED=0

                if ! write_memory "$target"; then
                    err "Shader applied, but selection memory could not be saved."
                fi

                notify_applied "$target"
                exit 0
                ;;

            10|11)
                # No matches: preserve the query and preview nothing.
                ((view_count == 0)) && continue

                if [[ "$selection" =~ ^[0-9]+$ ]] && ((selection >= 0 && selection < view_count)); then
                    view_pos=$selection
                    if ((exit_code == 10)); then
                        view_pos=$(((view_pos + 1) % view_count))
                    else
                        view_pos=$(((view_pos - 1 + view_count) % view_count))
                    fi
                elif ((exit_code == 10)); then
                    view_pos=0
                else
                    view_pos=$((view_count - 1))
                fi

                CURRENT_IDX=${view_idx[view_pos]}
                target="${SHADERS[CURRENT_IDX]}"

                RESTORE_NEEDED=1
                if ! apply_shader "$target"; then
                    err "Failed to preview the selected shader."
                    exit 1
                fi

                ACTIVE_IDX=$CURRENT_IDX
                ;;

            1)
                # EXIT cleanup restores only if an apply was attempted.
                exit 0
                ;;

            *)
                err "Rofi exited with unexpected code: $exit_code"
                exit 1
                ;;
        esac
    done
}

main() {
    local runtime_dir

    if (($# > 1)); then
        err "Usage: ${0##*/} [shader-dir]"
        exit 1
    fi

    if (($# == 1)); then
        if [[ -d "$1" ]]; then
            SHADER_DIR="$1"
        else
            err "Shader directory not found: $1"
            exit 1
        fi
    fi

    check_dependencies

    if [[ ! -d "$SHADER_DIR" ]]; then
        err "Shader directory not found: $SHADER_DIR"
        exit 1
    fi

    runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

    # Serialize entire sessions so one picker's cancel cannot undo
    # another picker's accepted selection.
    exec {LOCK_FD}>"$runtime_dir/dusky-shader-menu.lock"

    if ! flock -n "$LOCK_FD"; then
        err "Another shader picker is already running, or its lock is unavailable."
        exit 1
    fi

    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP

    if ! read_live_shader; then
        err "Could not read the current screen shader; nothing was changed."
        exit 1
    fi

    ORIGINAL_SHADER="$LIVE_SHADER"

    build_menu
    choose_initial_row
    main_loop
}

main "$@"
