#!/usr/bin/env bash
set -euo pipefail
shopt -s inherit_errexit

# ==============================================================================
# CONFIGURATION
# ==============================================================================

readonly DELIM=':::'

readonly -a MENU_COMMAND=(
    rofi
    -dmenu
    -i
    -no-custom
    -markup-rows
    -p 'Keybinds'
    -theme-str 'window {width: 53%;}'
    -theme-str 'listview {fixed-height: true;}'
)

readonly -a REQUIRED_COMMANDS=(
    gawk
    hyprctl
    jq
    luajit
    mktemp
    rofi
    sort
    xkbcli
)

KEYMAP_CACHE=''

# ==============================================================================
# HELPERS
# ==============================================================================

notify_error() {
    local title=$1
    local message=$2

    notify-send -u critical "$title" "$message" >/dev/null 2>&1 || \
        printf 'Error: %s\n' "$message" >&2
}

die() {
    notify_error "Keybind Error" "$1"
    exit 1
}

cleanup() {
    [[ -n ${KEYMAP_CACHE:-} ]] || return 0
    rm -f -- "$KEYMAP_CACHE"
}

check_dependencies() {
    local -a missing=()
    local cmd

    for cmd in "${REQUIRED_COMMANDS[@]}"; do
        command -v -- "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done

    ((${#missing[@]} == 0)) || die "Missing dependencies: ${missing[*]}"
}

get_hypr_xkb_option() {
    local option=$1
    local value

    if ! value=$(hyprctl -j getoption "input:$option" 2>/dev/null | jq -r '.str // empty' 2>/dev/null); then
        return 1
    fi

    [[ -n $value ]] || return 1
    printf '%s\n' "$value"
}

parse_keymap() {
    gawk '
        BEGIN {
            in_codes = 0
            in_syms = 0
        }

        /^[[:space:]]*xkb_keycodes([[:space:]]+"[^"]*")?[[:space:]]*{/ {
            in_codes = 1
            in_syms = 0
            next
        }

        /^[[:space:]]*xkb_symbols([[:space:]]+"[^"]*")?[[:space:]]*{/ {
            in_codes = 0
            in_syms = 1
            next
        }

        /^[[:space:]]*};[[:space:]]*$/ {
            in_codes = 0
            in_syms = 0
            next
        }

        in_codes && /<[A-Za-z0-9_]+>[[:space:]]*=[[:space:]]*[0-9]+/ {
            line = $0
            gsub(/[<>;]/, "", line)
            split(line, parts, /[[:space:]]*=[[:space:]]*/)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", parts[1])
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", parts[2])
            if (parts[1] != "" && parts[2] ~ /^[0-9]+$/) {
                code[parts[1]] = parts[2]
            }
            next
        }

        in_syms && /key[[:space:]]+<[A-Za-z0-9_]+>/ {
            if (!match($0, /<[A-Za-z0-9_]+>/)) {
                next
            }

            key_name = substr($0, RSTART + 1, RLENGTH - 2)

            if (match($0, /\[[^]]+\]/)) {
                split(substr($0, RSTART + 1, RLENGTH - 2), symbols, ",")
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", symbols[1])

                if ((key_name in code) && symbols[1] != "") {
                    printf "%s\t%s\n", code[key_name], symbols[1]
                }
            }
        }
    '
}

get_keymap() {
    local -a xkb_args=()
    local option
    local flag
    local value

    while IFS=$'\t' read -r option flag; do
        if value=$(get_hypr_xkb_option "$option"); then
            xkb_args+=("$flag" "$value")
        fi
    done <<'EOF'
kb_rules	--rules
kb_model	--model
kb_layout	--layout
kb_variant	--variant
kb_options	--options
EOF

    if ((${#xkb_args[@]} > 0)); then
        if xkbcli compile-keymap "${xkb_args[@]}" 2>/dev/null | parse_keymap; then
            return 0
        fi
    fi

    xkbcli compile-keymap 2>/dev/null | parse_keymap
}

get_binds() {
    local delim=$1
    local json_out

    if json_out=$(hyprctl -j binds 2>/dev/null) && jq -e . >/dev/null 2>&1 <<< "$json_out"; then
        jq -r --arg d "$delim" '
            def clean:
                tostring
                | gsub("\r\n?|\n"; " ");

            .[]
            | select((.dispatcher // "") != "")
            | select(((.key // "") != "") or (((.keycode // 0) | tonumber) > 0))
            | [
                (.submap // "" | clean),
                (.key // "" | clean),
                ((.keycode // 0) | tostring),
                ((.modmask // 0) | tostring),
                (.description // "" | clean),
                (.dispatcher // "" | clean),
                (.arg // "" | clean)
              ]
            | join($d)
        ' <<< "$json_out"
        return 0
    fi

    hyprctl binds 2>/dev/null | gawk -v delim="$delim" '
        function clean(str) {
            sub(/^[[:space:]]+/, "", str)
            sub(/[[:space:]]+$/, "", str)
            gsub(/[\r\n]+/, " ", str)
            return str
        }
        function emit() {
            if (dispatcher != "" && (key != "" || keycode > 0)) {
                printf "%s%s%s%s%d%s%d%s%s%s%s%s%s\n", clean(submap), delim, clean(key), delim, keycode, delim, modmask, delim, clean(description), delim, clean(dispatcher), delim, clean(arg)
            }
        }
        /^[^\t]/ {
            emit()
            submap = ""; key = ""; keycode = 0; modmask = 0; description = ""; dispatcher = ""; arg = ""
            next
        }
        /^\tmodmask:/ { modmask = int(substr($0, index($0, ":") + 1)) }
        /^\tsubmap:/ { submap = substr($0, index($0, ":") + 1) }
        /^\tkey:/ { key = substr($0, index($0, ":") + 1) }
        /^\tkeycode:/ { keycode = int(substr($0, index($0, ":") + 1)) }
        /^\tdescription:/ { description = substr($0, index($0, ":") + 1) }
        /^\tdispatcher:/ { dispatcher = substr($0, index($0, ":") + 1) }
        /^\targ:/ { arg = substr($0, index($0, ":") + 1) }
        END { emit() }
    '
}

build_rows() {
    local cache=$1
    local delim=$2
    local categorizer="${HOME}/user_scripts/hypr/input/rofi_keybinds/categorize_binds.py"

    get_binds "$delim" | "$categorizer" "$cache" "$delim"
}

main() {
    local data
    local menu_input
    local selected_index
    local selected_line
    local dispatcher
    local argument
    local record
    local -a records=()
    local -a menu_rows=()

    check_dependencies
    trap cleanup EXIT INT TERM HUP

    KEYMAP_CACHE=$(mktemp --tmpdir keybinds-keymap.XXXXXXXXXX) || exit 1

    if ! get_keymap > "$KEYMAP_CACHE"; then
        : > "$KEYMAP_CACHE"
    fi

    if ! data=$(build_rows "$KEYMAP_CACHE" "$DELIM"); then
        die "Failed to query Hyprland binds."
    fi

    [[ -n $data ]] || exit 0

    mapfile -t records <<< "$data"

    local script_path="${HOME}/user_scripts/hypr/input/keybinds_cheatsheet.py"
    local cheatsheet_cmd="kitty --class DuskyKeybindsCheatsheet --title \"Dusky Keybinds Cheatsheet\" -e python3 ${script_path}"
    local cheatsheet_row="󰌌  <span background=\"#fab387\" foreground=\"#11111b\" weight=\"bold\"> CHEATSHEET </span> <span weight=\"bold\">Dusky Keybinds Cheatsheet</span>${DELIM}exec${DELIM}${cheatsheet_cmd}"
    records=("$cheatsheet_row" "${records[@]}")

    for record in "${records[@]}"; do
        menu_rows+=("${record%%$DELIM*}")
    done

    printf -v menu_input '%s\n' "${menu_rows[@]}"

    selected_index=$("${MENU_COMMAND[@]}" -format i <<< "$menu_input") || exit 0

    [[ $selected_index =~ ^[0-9]+$ ]] || exit 0
    (( selected_index >= 0 && selected_index < ${#records[@]} )) || exit 0

    selected_line=${records[selected_index]}

    local nl=$'\n'
    local -a fields=()
    mapfile -t fields <<< "${selected_line//$DELIM/$nl}"

    dispatcher=${fields[1]:-}
    argument=${fields[2]:-}
    local description=${fields[3]:-}

    local dispatch_helper="${HOME}/user_scripts/hypr/input/rofi_keybinds/dispatch_hypr_bind.lua"

    if [[ $dispatcher == "header" ]]; then
        exit 0
    elif [[ $dispatcher == "exec" || $dispatcher == "exec_cmd" ]]; then
        eval "$argument" >/dev/null 2>&1 &
    elif [[ -x $dispatch_helper ]]; then
        "$dispatch_helper" "$description" "$dispatcher" "$argument" || die "Failed to dispatch keybinding: ${description:-$dispatcher}"
    elif [[ -n $argument ]]; then
        hyprctl dispatch "$dispatcher" "$argument" || die "Failed to dispatch: $dispatcher $argument"
    else
        hyprctl dispatch "$dispatcher" || die "Failed to dispatch: $dispatcher"
    fi
}

main "$@"
