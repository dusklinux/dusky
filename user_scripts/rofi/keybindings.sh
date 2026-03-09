#!/usr/bin/env bash
set -euo pipefail
shopt -s inherit_errexit

# ==============================================================================
# CONFIGURATION
# ==============================================================================

readonly DELIM=$'\x1f'

readonly -a MENU_COMMAND=(
    rofi
    -dmenu
    -i
    -no-custom
    -markup-rows
    -p 'Keybinds'
    -theme-str 'window {width: 43%;}'
    -theme-str 'listview {fixed-height: true;}'
)

readonly -a REQUIRED_COMMANDS=(
    gawk
    hyprctl
    jq
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

        in_codes && /<[A-Z0-9]+>[[:space:]]*=[[:space:]]*[0-9]+/ {
            line = $0
            gsub(/[<>;]/, "", line)
            split(line, parts, /[[:space:]]*=[[:space:]]*/)
            if (parts[1] != "" && parts[2] ~ /^[0-9]+$/) {
                code[parts[1]] = parts[2]
            }
            next
        }

        in_syms && /key[[:space:]]+<[A-Z0-9]+>/ {
            if (!match($0, /<[A-Z0-9]+>/)) {
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

    hyprctl -j binds 2>/dev/null | jq -r --arg d "$delim" '
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
    '
}

build_rows() {
    local cache=$1
    local delim=$2

    get_binds "$delim" | gawk -F"$delim" -v delim="$delim" -v cache="$cache" '
        BEGIN {
            while ((getline < cache) > 0) {
                split($0, parts, "\t")
                if (parts[1] != "") {
                    keymap[parts[1]] = parts[2]
                }
            }
            close(cache)
        }

        function esc(text) {
            gsub(/\r/, " ", text)
            gsub(/\n/, " ", text)
            gsub(/&/, "\\&amp;", text)
            gsub(/</, "\\&lt;", text)
            gsub(/>/, "\\&gt;", text)
            return text
        }

        function icon_for(dispatcher) {
            if (dispatcher ~ /^exec/)  return " "
            if (dispatcher ~ /kill/)   return " "
            if (dispatcher ~ /resize/) return "󰩨 "
            if (dispatcher ~ /move/)   return "󰆾 "
            if (dispatcher ~ /float/)  return " "
            if (dispatcher ~ /full/)   return " "
            if (dispatcher ~ /work/)   return " "
            if (dispatcher ~ /pass/)   return " "
            return " "
        }

        function mods_for(mask, out) {
            out = ""
            if (and(mask, 1))   out = out "SHIFT "
            if (and(mask, 2))   out = out "CAPS "
            if (and(mask, 4))   out = out "CTRL "
            if (and(mask, 8))   out = out "ALT "
            if (and(mask, 16))  out = out "MOD2 "
            if (and(mask, 32))  out = out "MOD3 "
            if (and(mask, 64))  out = out "SUPER "
            if (and(mask, 128)) out = out "MOD5 "
            sub(/[[:space:]]+$/, "", out)
            return out
        }

        {
            submap = $1
            key = $2
            keycode = int($3)
            modmask = int($4)
            description = $5
            dispatcher = $6
            argument = $7

            if (key !~ /^mouse:/ && keycode > 0 && (keycode in keymap)) {
                key = keymap[keycode]
            } else if (key == "" && keycode > 0) {
                key = sprintf("code:%d", keycode)
            }

            key = toupper(key)
            mods = mods_for(modmask)

            display_key = sprintf("<span alpha=\"65%%\">%s</span> <span weight=\"bold\">%s</span>", esc(sprintf("%-7s", mods)), esc(sprintf("%-10s", key)))

            if (description != "") {
                action = esc(description)
            } else if (argument != "") {
                action = sprintf("%s <span alpha=\"50%%\" style=\"italic\">(%s)</span>", esc(dispatcher), esc(argument))
            } else {
                action = esc(dispatcher)
            }

            if (submap != "" && submap != "global") {
                action = sprintf("<span weight=\"bold\" foreground=\"#f38ba8\">[%s]</span> %s", esc(toupper(submap)), action)
            }

            printf "%s  %s  %s%s%s%s%s\n", icon_for(dispatcher), display_key, action, delim, dispatcher, delim, argument
        }
    '
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

    if ! data=$(
        build_rows "$KEYMAP_CACHE" "$DELIM" |
        LC_ALL=C sort -t"$DELIM" -k1,1 -k2,2 -k3,3 -u
    ); then
        die "Failed to query Hyprland binds."
    fi

    [[ -n $data ]] || exit 0

    mapfile -t records <<< "$data"

    for record in "${records[@]}"; do
        menu_rows+=("${record%%$DELIM*}")
    done

    printf -v menu_input '%s\n' "${menu_rows[@]}"

    selected_index=$("${MENU_COMMAND[@]}" -format i <<< "$menu_input") || exit 0

    [[ $selected_index =~ ^[0-9]+$ ]] || exit 0
    (( selected_index >= 0 && selected_index < ${#records[@]} )) || exit 0

    selected_line=${records[selected_index]}
    IFS=$DELIM read -r _ dispatcher argument <<< "$selected_line"

    if [[ -n $argument ]]; then
        hyprctl dispatch "$dispatcher" "$argument" || die "Failed to dispatch: $dispatcher $argument"
    else
        hyprctl dispatch "$dispatcher" || die "Failed to dispatch: $dispatcher"
    fi
}

main "$@"
