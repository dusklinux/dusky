#!/usr/bin/env bash
#
# Hyprland categorized shader picker with Up/Down live preview.
# Requires: rofi, hyprctl, flock, python3.
# Uses glslangValidator for preflight when installed.
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
    -theme-str 'window { width: 440px; } listview { lines: 12; }'
)

# Global shader catalog: index 0 is Turn Off (""); 1..N are real shader paths.
declare -a SHADERS=("")
declare -a MENU_LINES=("󰜺  Turn Off")
declare -a SHADER_CATS=("off")

# Category index lists (indices into SHADERS / MENU_LINES)
declare -a CAT_ALL=(0)
declare -a CAT_COLOR=()
declare -a CAT_RETRO=()
declare -a CAT_CURSOR=()
declare -a CAT_AMBIENT=()
declare -a CAT_EXTREME=()

declare ORIGINAL_SHADER=""
declare LIVE_SHADER=""
declare STATE_FILE=""
declare ORIGINAL_DAMAGE=""
declare ORIGINAL_VFR=""
declare LIVE_DAMAGE=""
declare LIVE_VFR=""
declare BASE_DAMAGE=""
declare BASE_VFR=""
declare TARGET_DAMAGE=""
declare TARGET_VFR=""
declare SEARCH_QUERY=""
declare REMEMBERED_SHADER=""
declare REMEMBERED_CATEGORY=""
declare LOCK_FD=""

declare -i CURRENT_IDX=-1
declare -i ORIGINAL_ACTIVE_IDX=-1
declare -i ACTIVE_IDX=-1
declare -i REMEMBERED_IDX=-1
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

    json=$(hyprctl -j --batch 'getoption decoration:screen_shader;getoption debug:damage_tracking;getoption debug:vfr') || return 1

    mapfile -d '' -t values < <(
        python3 -c '
import json
import sys

try:
    raw = sys.stdin.read().strip()
    decoder = json.JSONDecoder()
    options = []
    while raw:
        option, end = decoder.raw_decode(raw)
        options.append(option)
        raw = raw[end:].lstrip()
    data, damage, vfr = options
    if not isinstance(data, dict) or not isinstance(data.get("str"), str):
        raise ValueError("expected a string-valued str member")
    value = data["str"]
    if value == "[[EMPTY]]":
        value = ""
    damage = damage["int"]
    vfr = vfr["bool"]
    if type(damage) is not int or damage not in (0, 1, 2) or type(vfr) is not bool:
        raise ValueError("invalid rendering settings")
except (ValueError, TypeError, KeyError) as exc:
    print(f"Cannot parse screen_shader state: {exc}", file=sys.stderr)
    sys.exit(1)

sys.stdout.write(value + "\0" + str(damage) + "\0" + str(vfr).lower() + "\0")
' <<< "$json"
    )

    ((${#values[@]} == 3)) || return 1
    LIVE_SHADER="${values[0]}"
    LIVE_DAMAGE="${values[1]}"
    LIVE_VFR="${values[2]}"
}

# A session-scoped record lets the next picker restore the settings from
# before an accepted spatial/animated effect. Ignore stale records after
# another tool or a config reload changes the shader or rendering settings.
read_baseline() {
    local -a values=()
    BASE_DAMAGE=$ORIGINAL_DAMAGE
    BASE_VFR=$ORIGINAL_VFR
    mapfile -d '' -t values < <(python3 - "$STATE_FILE" "$ORIGINAL_SHADER" "$ORIGINAL_DAMAGE" "$ORIGINAL_VFR" <<'PY'
import json
import sys
from pathlib import Path

try:
    state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if (state["shader"] != sys.argv[2] or str(state["damage"]) != sys.argv[3]
            or str(state["vfr"]).lower() != sys.argv[4]):
        sys.exit(0)
    damage, vfr = state["base_damage"], state["base_vfr"]
    if type(damage) is not int or damage not in (0, 1, 2) or type(vfr) is not bool:
        sys.exit(0)
except (OSError, ValueError, TypeError, KeyError):
    sys.exit(0)
sys.stdout.write(str(damage) + "\0" + str(vfr).lower() + "\0")
PY
    )
    if ((${#values[@]} == 2)); then
        BASE_DAMAGE=${values[0]}
        BASE_VFR=${values[1]}
    fi
}

write_state() {
    python3 - "$STATE_FILE" "$LIVE_SHADER" "$LIVE_DAMAGE" "$LIVE_VFR" "$BASE_DAMAGE" "$BASE_VFR" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if sys.argv[3:5] == sys.argv[5:7]:
    path.unlink(missing_ok=True)
else:
    state = dict(shader=sys.argv[2], damage=int(sys.argv[3]), vfr=sys.argv[4] == "true",
                 base_damage=int(sys.argv[5]), base_vfr=sys.argv[6] == "true")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state) + "\n", encoding="utf-8")
    temporary.replace(path)
PY
}

shader_policy() {
    local path="$1" line
    TARGET_DAMAGE=$BASE_DAMAGE
    TARGET_VFR=$BASE_VFR
    [[ -n "$path" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            '// dusky: full-redraw')
                ((TARGET_DAMAGE == 0)) || TARGET_DAMAGE=1
                ;;
            '// dusky: animated')
                TARGET_DAMAGE=0
                TARGET_VFR=false
                ;;
        esac
    done < "$path"
}

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

    if (($# == 1)) && [[ -n "$path" && ! -f "$path" ]]; then
        err "Shader file no longer exists: $path"
        return 1
    fi

    if (($# == 1)) && [[ -n "$path" ]] && command -v glslangValidator >/dev/null 2>&1; then
        if ! glslangValidator -S frag "$path" >&2; then
            err "Shader syntax check failed: $path"
            return 1
        fi
    fi

    if (($# == 3)); then
        TARGET_DAMAGE=$2
        TARGET_VFR=$3
    else
        shader_policy "$path" || return 1
    fi

    lua=$(python3 - "$path" "$TARGET_DAMAGE" "$TARGET_VFR" <<'PY'
import os
import sys

encoded = "".join(f"\\{byte:03d}" for byte in os.fsencode(sys.argv[1]))
print('hl.config({ decoration = { screen_shader = "' + encoded
      + '" }, debug = { damage_tracking = ' + sys.argv[2] + ', vfr = ' + sys.argv[3] + ' } })')
PY
    ) || return 1

    hyprctl eval "$lua" >/dev/null || return 1
    read_live_shader || return 1

    if [[ "$LIVE_SHADER" != "$path" || "$LIVE_DAMAGE" != "$TARGET_DAMAGE" || "$LIVE_VFR" != "$TARGET_VFR" ]]; then
        err "Shader/rendering readback does not match the requested values."
        return 1
    fi

    return 0
}

restore_original_if_needed() {
    if ((RESTORE_NEEDED)); then
        if ! apply_shader "$ORIGINAL_SHADER" "$ORIGINAL_DAMAGE" "$ORIGINAL_VFR"; then
            err "Failed to restore the original screen shader."
            return 1
        fi
        RESTORE_NEEDED=0
        ACTIVE_IDX=$ORIGINAL_ACTIVE_IDX
    fi
    return 0
}

# Read both shader_menu and shader_category from MEMORY_FILE.
read_memory() {
    local -a values=()
    mapfile -d '' -t values < <(
        python3 - "$MEMORY_FILE" <<'PY'
import json
import sys
from pathlib import Path

shader_val = ""
cat_val = ""

try:
    lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
except OSError:
    sys.stdout.write("\0\0")
    sys.exit(0)

for line in lines:
    if line.startswith("shader_menu="):
        raw = line[len("shader_menu="):]
        if raw == "off":
            shader_val = ""
        else:
            try:
                parsed = json.loads(raw)
                shader_val = parsed if isinstance(parsed, str) else raw
            except ValueError:
                shader_val = raw
    elif line.startswith("shader_category="):
        cat_val = line[len("shader_category="):].strip()

sys.stdout.write(shader_val + "\0" + cat_val + "\0")
PY
    )
    if ((${#values[@]} >= 2)); then
        REMEMBERED_SHADER="${values[0]}"
        REMEMBERED_CATEGORY="${values[1]}"
    fi
}

write_memory() {
    local path="$1"
    local category="${2:-}"

    mkdir -p -- "${MEMORY_FILE%/*}" || return 1

    python3 - "$MEMORY_FILE" "$path" "$category" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
shader_path = sys.argv[2]
category = sys.argv[3]

try:
    lines = path.read_text(encoding="utf-8").splitlines()
except FileNotFoundError:
    lines = []

lines = [
    line for line in lines
    if not line.startswith("shader_menu=") and not line.startswith("shader_category=")
]
lines.append("shader_menu=" + json.dumps(shader_path, ensure_ascii=True))
if category:
    lines.append(f"shader_category={category}")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

classify_shader_category() {
    local filename="$1"
    local stem="${filename%.*}"
    local lower="${stem,,}"
    local num_str=""
    local -i num=0

    if [[ "$lower" =~ ^([0-9]+)[_-] ]]; then
        num_str="${BASH_REMATCH[1]}"
        num=$((10#$num_str))
        if ((num >= 1 && num <= 15)); then
            printf 'color'
            return 0
        elif ((num >= 16 && num <= 22)); then
            printf 'retro'
            return 0
        elif ((num >= 23 && num <= 33)); then
            printf 'cursor'
            return 0
        elif ((num >= 34 && num <= 44)); then
            printf 'ambient'
            return 0
        elif ((num >= 45)); then
            printf 'extreme'
            return 0
        fi
    fi

    case "$lower" in
        *tint*|*kelvin*|*phosphor*|*sepia*|*gray*|*grey*|*invert*|*poster*|*vibrance*|*saturat*|*contrast*)
            printf 'color' ;;
        *click*|*shockwave*|*pulse*|*bubble*|*cursor*|*pointer*|*spotlight*|*ferrofluid*|*kaleidoscope*)
            printf 'cursor' ;;
        *wormhole*|*black_hole*|*tear*|*fracture*|*glitch_storm*|*melt*|*sphere*|*mirror*)
            printf 'extreme' ;;
        *canopy*|*komorebi*|*rain*|*glass*|*haze*|*aurora*|*wobble*|*ripple*|*warp*|*water*|*marble*|*tide*)
            printf 'ambient' ;;
        *)
            printf 'retro' ;;
    esac
}

build_menu() {
    local file
    local label
    local cat
    local -i idx

    shopt -s nullglob dotglob

    for file in "$SHADER_DIR/"*.glsl "$SHADER_DIR/"*.frag; do
        [[ -f "$file" ]] || continue

        SHADERS+=("$file")
        idx=$((${#SHADERS[@]} - 1))

        label="${file##*/}"
        cat=$(classify_shader_category "$label")

        label=${label//\\/\\\\}
        label=${label//$'\n'/'\n'}
        label=${label//$'\r'/'\r'}
        label=${label//$'\t'/'\t'}

        MENU_LINES+=("$label")
        SHADER_CATS+=("$cat")
        CAT_ALL+=("$idx")

        case "$cat" in
            color)   CAT_COLOR+=("$idx") ;;
            retro)   CAT_RETRO+=("$idx") ;;
            cursor)  CAT_CURSOR+=("$idx") ;;
            ambient) CAT_AMBIENT+=("$idx") ;;
            extreme) CAT_EXTREME+=("$idx") ;;
        esac
    done
}

resolve_initial_state() {
    local i
    local name
    local -i legacy_idx=-1
    local -i legacy_count=0

    find_shader_index "$ORIGINAL_SHADER"
    ORIGINAL_ACTIVE_IDX=$MATCH_IDX
    ACTIVE_IDX=$MATCH_IDX

    read_memory

    if [[ -n "$REMEMBERED_SHADER" ]]; then
        find_shader_index "$REMEMBERED_SHADER"
        if ((MATCH_IDX >= 0)); then
            REMEMBERED_IDX=$MATCH_IDX
        else
            for i in "${!SHADERS[@]}"; do
                [[ -n "${SHADERS[i]}" ]] || continue
                name="${SHADERS[i]##*/}"
                if [[ "${name%.*}" == "$REMEMBERED_SHADER" ]]; then
                    legacy_idx=$i
                    legacy_count=$((legacy_count + 1))
                fi
            done
            if ((legacy_count == 1)); then
                REMEMBERED_IDX=$legacy_idx
            fi
        fi
    fi
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

commit_and_exit() {
    local target="$1"
    local category="$2"

    RESTORE_NEEDED=1
    if ! apply_shader "$target"; then
        err "Failed to apply the selected shader."
        exit 1
    fi

    if ! write_state; then
        err "Could not save rendering settings; restoring the previous shader."
        exit 1
    fi
    RESTORE_NEEDED=0

    if ! write_memory "$target" "$category"; then
        err "Shader applied, but selection memory could not be saved."
    fi

    notify_applied "$target"
    exit 0
}

cleanup() {
    local status=$?

    trap '' INT TERM HUP

    if ((RESTORE_NEEDED)); then
        if ! apply_shader "$ORIGINAL_SHADER" "$ORIGINAL_DAMAGE" "$ORIGINAL_VFR"; then
            err "Failed to restore the original screen shader."
            if ((status == 0)); then
                status=1
            fi
        fi
    fi

    exit "$status"
}

# Build the currently visible rows for a category submenu and SEARCH_QUERY.
# Index -1 represents "  Back"; index >= 0 indexes into SHADERS/MENU_LINES.
build_sub_view() {
    local -n _sub_source=$1
    local -n _idx=$2
    local -n _lines=$3
    local item_idx label hay tok
    local -a tokens=()

    _idx=()
    _lines=()

    if [[ -z "${SEARCH_QUERY//[[:space:]]/}" ]]; then
        _idx+=(-1)
        _lines+=("  Back")
        for item_idx in "${_sub_source[@]}"; do
            _idx+=("$item_idx")
            _lines+=("${MENU_LINES[item_idx]}")
        done
        return 0
    fi

    read -ra tokens <<< "${SEARCH_QUERY,,}" || true

    for item_idx in "${_sub_source[@]}"; do
        label="${MENU_LINES[item_idx]}"
        hay="${label,,}"
        for tok in "${tokens[@]}"; do
            [[ "$hay" == *"$tok"* ]] || continue 2
        done
        _idx+=("$item_idx")
        _lines+=("$label")
    done
}

# Run a live-preview shader submenu for a specific category array.
submenu_shaders() {
    local cat_id="$1"
    local cat_title="$2"
    local -n _cat_items=$3

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
    local -i selected_idx
    local -a flags=()
    local -a view_idx=()
    local -a view_lines=()

    SEARCH_QUERY=""
    CURRENT_IDX=-1

    # If the currently active shader is in this category, start on it;
    # otherwise start on "  Back" so pressing Down previews the first shader.
    for i in "${_cat_items[@]}"; do
        if ((i > 0 && i == ACTIVE_IDX)); then
            CURRENT_IDX=$i
            break
        fi
    done

    while true; do
        build_sub_view "$3" view_idx view_lines
        view_count=${#view_idx[@]}

        if ((view_count > 0)); then
            printf -v view_text '%s\n' "${view_lines[@]}"
            view_text="${view_text%$'\n'}"
        else
            view_text=""
        fi

        sel_row=0
        for i in "${!view_idx[@]}"; do
            if ((view_idx[i] == CURRENT_IDX)); then
                sel_row=$i
                break
            fi
        done

        if [[ -n "$SEARCH_QUERY" ]]; then
            query_display="$cat_title [$SEARCH_QUERY]"
        else
            query_display="$cat_title"
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
                if [[ "$selection" =~ ^[0-9]+$ ]] && ((selection >= 0 && selection < view_count)); then
                    selected_idx=${view_idx[selection]}
                else
                    continue
                fi

                # "  Back" selected -> restore baseline preview and return to Category Menu
                if ((selected_idx == -1)); then
                    restore_original_if_needed || exit 1
                    return 0
                fi

                target="${SHADERS[selected_idx]}"
                commit_and_exit "$target" "$cat_id"
                ;;

            10|11)
                selected_idx=-2
                if [[ "$selection" =~ ^[0-9]+$ ]] && ((selection < view_count)); then
                    selected_idx=${view_idx[selection]}
                fi
                build_sub_view "$3" view_idx view_lines
                view_count=${#view_idx[@]}
                ((view_count == 0)) && continue

                view_pos=-1
                for i in "${!view_idx[@]}"; do
                    if ((view_idx[i] == selected_idx)); then
                        view_pos=$i
                        break
                    fi
                done

                if ((view_pos >= 0)); then
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

                # Highlighting "  Back" restores baseline shader so you can A/B compare
                if ((CURRENT_IDX == -1)); then
                    restore_original_if_needed || exit 1
                    continue
                fi

                target="${SHADERS[CURRENT_IDX]}"
                RESTORE_NEEDED=1
                if ! apply_shader "$target"; then
                    err "Failed to preview the selected shader."
                    exit 1
                fi
                ACTIVE_IDX=$CURRENT_IDX
                ;;

            1)
                # First Esc clears an active search filter; second Esc returns to Category Menu
                if [[ -n "${SEARCH_QUERY//[[:space:]]/}" ]]; then
                    SEARCH_QUERY=""
                    continue
                fi
                restore_original_if_needed || exit 1
                return 0
                ;;

            *)
                err "Rofi exited with unexpected code: $exit_code"
                exit 1
                ;;
        esac
    done
}

category_to_row() {
    case "${1:-}" in
        off)     printf '0' ;;
        all)     printf '1' ;;
        color)   printf '2' ;;
        retro)   printf '3' ;;
        cursor)  printf '4' ;;
        ambient) printf '5' ;;
        extreme) printf '6' ;;
        *)       printf '1' ;;
    esac
}

category_menu_loop() {
    local choice=""
    local active_cat=""
    local -i sel_row=1
    local -i act_row=-1
    local -i exit_code=0
    local -i total_shaders=$((${#SHADERS[@]} - 1))

    # Determine initial highlighted category row
    if ((ORIGINAL_ACTIVE_IDX > 0)); then
        active_cat="${SHADER_CATS[ORIGINAL_ACTIVE_IDX]}"
        sel_row=$(category_to_row "$active_cat")
    elif [[ -n "$REMEMBERED_CATEGORY" ]]; then
        sel_row=$(category_to_row "$REMEMBERED_CATEGORY")
    elif ((REMEMBERED_IDX > 0)); then
        sel_row=$(category_to_row "${SHADER_CATS[REMEMBERED_IDX]}")
    fi

    while true; do
        local -a cat_options=(
            "󰜺  Turn Off"
            "󰸉  All Shaders ($total_shaders)"
            "󰏘  Color & Tone (${#CAT_COLOR[@]})"
            "󰄄  Retro & Optics (${#CAT_RETRO[@]})"
            "󰇀  Cursor & Click (${#CAT_CURSOR[@]})"
            "󰖌  Ambient & Fluid (${#CAT_AMBIENT[@]})"
            "󱄄  Extreme & Surreal (${#CAT_EXTREME[@]})"
            "  Cancel"
        )

        local -a flags=(
            -p "Shaders"
            -format 'i'
            -selected-row "$sel_row"
        )

        if ((ORIGINAL_ACTIVE_IDX > 0)); then
            act_row=$(category_to_row "${SHADER_CATS[ORIGINAL_ACTIVE_IDX]}")
            flags+=(-a "$act_row")
        fi

        exit_code=0
        choice=$(printf '%s\n' "${cat_options[@]}" | "${ROFI_CMD[@]}" "${flags[@]}") || exit_code=$?

        if ((exit_code != 0)); then
            exit 0
        fi

        [[ "$choice" =~ ^[0-9]+$ ]] || continue
        sel_row=$choice

        case "$choice" in
            0) commit_and_exit "" "off" ;;
            1) submenu_shaders "all"     "All Shaders"       CAT_ALL ;;
            2) submenu_shaders "color"   "Color & Tone"      CAT_COLOR ;;
            3) submenu_shaders "retro"   "Retro & Optics"    CAT_RETRO ;;
            4) submenu_shaders "cursor"  "Cursor & Click"    CAT_CURSOR ;;
            5) submenu_shaders "ambient" "Ambient & Fluid"   CAT_AMBIENT ;;
            6) submenu_shaders "extreme" "Extreme & Surreal" CAT_EXTREME ;;
            7) exit 0 ;;
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
            [[ "$SHADER_DIR" == /* ]] || SHADER_DIR="$PWD/$SHADER_DIR"
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
    STATE_FILE="$runtime_dir/dusky-shader-${HYPRLAND_INSTANCE_SIGNATURE:-default}.json"

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
    ORIGINAL_DAMAGE=$LIVE_DAMAGE
    ORIGINAL_VFR=$LIVE_VFR
    read_baseline

    build_menu
    resolve_initial_state
    category_menu_loop
}

main "$@"
