#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky Matugen TUI - Dynamic TOML Manager
# Target: Arch Linux / Hyprland / UWSM / Wayland
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s extglob

# =============================================================================
# ▼ DUSKY MATUGEN TUI - CONFIGURATION GUIDE ▼
# =============================================================================
# How to add a new template to the TUI manually:
# 1. Ensure your TOML block in config.toml is formatted properly:
#    [templates.my_new_app]
#    ...
# 2. Register it below in the 'register_items' function using this syntax:
#    register_template <Tab_Index> "Display Name" "toml_key" "default_state" "check_cmd"
#
# Parameters:
# - Tab_Index:     0=GTK, 1=System, 2=Apps, 3=Media. (Auto-discovery uses Tab 4).
# - Display Name:  What shows up in the TUI list.
# - toml_key:      The exact string after [templates. (e.g., "my_new_app").
# - default_state: "true" (uncommented) or "false" (commented) for the 'r' key / --default.
# - check_cmd:     (Optional) The binary name to check for when using --smart.
#                  If left blank, --smart falls back to the default_state.
#
# Note on Auto-Discovery:
# Any [templates.*] block in your TOML that is NOT explicitly registered here
# will automatically be placed into a dynamic "Discovered" tab at runtime.
# Discovered items default to "false" on reset and bypass --smart package checks.
# =============================================================================

# POINT THIS TO YOUR REAL MATUGEN CONFIG FILE
declare -r CONFIG_FILE="${HOME}/.config/matugen/config.toml"
declare -r APP_TITLE="Matugen Theme Configurator"
declare -r APP_VERSION="v5.1.0 (Auto-Discover)"

# Dimensions & Layout
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=76
declare -ri ADJUST_THRESHOLD=38
declare -ri ITEM_PADDING=32

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

# Notice: TABS is no longer readonly (-ra) so we can append discovered tabs
declare -a TABS=("GTK & Qt" "System" "Apps" "Media & Misc")

declare -A CHECK_CMDS=()

# Registration syntax:
# register_template <Tab_Index> <Display_Name> <TOML_Key> <Default_State> [Check_Command]
register_items() {
    # Tab 0: GTK & Qt
    register_template 0 "GTK 3"          "gtk3"       "true"  ""
    register_template 0 "GTK 4"          "gtk4"       "true"  ""
    register_template 0 "Icon Theme"     "icon_theme" "true"  ""
    register_template 0 "Qt5 CT"         "qt5ct"      "true"  ""
    register_template 0 "Qt6 CT"         "qt6ct"      "true"  ""

    # Tab 1: System
    register_template 1 "Hyprland"       "hyprland"   "true"  ""
    register_template 1 "Hyprlock"       "hyprlock"   "true"  ""
    register_template 1 "Waybar"         "waybar"     "true"  "waybar"
    register_template 1 "SwayOSD"        "swayosd"    "true"  "swayosd-server"
    register_template 1 "SwayNC"         "swaync"     "true"  "swaync"
    register_template 1 "Wlogout"        "wlogout"    "true"  "wlogout"
    register_template 1 "Rofi"           "rofi"       "true"  "rofi"
    register_template 1 "Dusky Control"  "dusky_control_center" "true" ""
    register_template 1 "Dusky Sliders"  "dusky_sliders"        "true" ""
    register_template 1 "Hyprpolkit"     "hyprpolkitagent"      "true" ""

    # Tab 2: Apps
    register_template 2 "Kitty"          "kitty"      "true"  "kitty"
    register_template 2 "OpenCode"       "opencode"   "false" "opencode"
    register_template 2 "VS Code"        "vscode"     "false" "vscodium"
    register_template 2 "Alacritty"      "alacritty"  "false" "alacritty"
    register_template 2 "Steam"          "steam"      "false" "steam"
    register_template 2 "NeoVim"         "neovim"     "true"  "nvim"
    register_template 2 "Zed Editor"     "zed"        "false" "zeditor"
    register_template 2 "Yazi"           "yazi"       "true"  "yazi"
    register_template 2 "Zathura"        "zathura"    "false" "zathura"
    register_template 2 "Starship"       "starship"   "false"  ""
    register_template 2 "Tmux"           "tmux"       "false" "tmux"
    register_template 2 "Obsidian"       "obsidian"   "false" "obsidian"

    # Tab 3: Media & Misc
    register_template 3 "OBS Studio"     "obs"        "false" "obs"
    register_template 3 "Vesktop"        "vesktop"    "false" "vesktop"
    register_template 3 "Beeper"         "beeper"     "false" "beeper"
    register_template 3 "Spicetify"      "spicetify"  "false" "spicetify"
    register_template 3 "Cava"           "cava"       "true" ""
    register_template 3 "Dump All Matugen Colors" "master_dump" "false" ""
    register_template 3 "Btop"           "btop"       "true" ""
    register_template 3 "Pywalfox"       "pywalfox"   "true" "pywalfox"
}

# Post-Write Hook (Triggered when TUI makes a change)
post_write_action() {
    : # You can add matugen reload commands here if desired
}

# =============================================================================
# ▲ END OF USER CONFIGURATION ▲
# =============================================================================

# --- Pre-computed Constants & State ---
declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" ''
declare -r H_LINE="${_h_line_buf// /─}"
unset _h_line_buf

# ANSI Constants
declare -r C_RESET=$'\033[0m'
declare -r C_CYAN=$'\033[1;36m'
declare -r C_GREEN=$'\033[1;32m'
declare -r C_MAGENTA=$'\033[1;35m'
declare -r C_RED=$'\033[1;31m'
declare -r C_YELLOW=$'\033[1;33m'
declare -r C_WHITE=$'\033[1;37m'
declare -r C_GREY=$'\033[1;30m'
declare -r C_INVERSE=$'\033[7m'
declare -r CLR_EOL=$'\033[K'
declare -r CLR_EOS=$'\033[J'
declare -r CLR_SCREEN=$'\033[2J'
declare -r CURSOR_HOME=$'\033[H'
declare -r CURSOR_HIDE=$'\033[?25l'
declare -r CURSOR_SHOW=$'\033[?25h'
declare -r MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h'
declare -r MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l'
declare -r ESC_READ_TIMEOUT=0.10
declare -r UNSET_MARKER='«unset»'

declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=0
declare -i SCROLL_OFFSET=0

# Notice: TAB_COUNT is dynamic now
declare -i TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare -i TAB_SCROLL_START=0
declare ORIGINAL_STTY=""

declare -i CURRENT_VIEW=0
declare CURRENT_MENU_ID=""
declare -i PARENT_ROW=0
declare -i PARENT_SCROLL=0

declare _TMPFILE="" _TMPMODE="" WRITE_TARGET=""
declare -i TERM_ROWS=0 TERM_COLS=0
declare -ri MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
declare -ri MIN_TERM_ROWS=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 5 ))

declare -gi LAST_WRITE_CHANGED=0
declare -gi UI_ACTIVE=0
declare STATUS_MESSAGE=""

declare LEFT_ARROW_ZONE="" RIGHT_ARROW_ZONE=""

declare -A ITEM_MAP=() VALUE_CACHE=() CONFIG_CACHE=() DEFAULTS=()

for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    declare -ga "TAB_ITEMS_${_ti}=()"
done
unset _ti

# --- System & Core Helpers ---

log_err() { printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; }
set_status() { declare -g STATUS_MESSAGE="$1"; }
clear_status() { declare -g STATUS_MESSAGE=""; }

cleanup() {
    if (( UI_ACTIVE )); then
        printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" >/dev/tty 2>/dev/null || :
        [[ -n "${ORIGINAL_STTY:-}" ]] && stty "$ORIGINAL_STTY" < /dev/tty 2>/dev/null || :
        printf '\n' >/dev/tty 2>/dev/null || :
    fi

    [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" ]] && rm -f -- "$_TMPFILE" 2>/dev/null || :
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

resolve_write_target() { WRITE_TARGET=$(realpath -e -- "$CONFIG_FILE"); }

create_tmpfile() {
    local target_dir target_base
    target_dir=$(dirname -- "$WRITE_TARGET")
    target_base=$(basename -- "$WRITE_TARGET")
    if ! _TMPFILE=$(mktemp --tmpdir="$target_dir" ".${target_base}.tmp.XXXXXXXXXX" 2>/dev/null); then
        _TMPFILE=""
        _TMPMODE=""
        return 1
    fi
    _TMPMODE="atomic"
    return 0
}

commit_tmpfile() {
    [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" && "${_TMPMODE:-}" == "atomic" ]] || return 1
    chmod --reference="$WRITE_TARGET" -- "$_TMPFILE" 2>/dev/null || return 1
    mv -f -- "$_TMPFILE" "$WRITE_TARGET" || return 1
    _TMPFILE=""
    _TMPMODE=""
    return 0
}

update_terminal_size() {
    local size
    if size=$(stty size < /dev/tty 2>/dev/null); then
        TERM_ROWS=${size%% *}
        TERM_COLS=${size##* }
    else
        TERM_ROWS=0
        TERM_COLS=0
    fi
}

terminal_size_ok() { (( TERM_COLS >= MIN_TERM_COLS && TERM_ROWS >= MIN_TERM_ROWS )); }

draw_small_terminal_notice() {
    printf '%s%s%sTerminal too small%s\n' "$CURSOR_HOME" "$CLR_SCREEN" "$C_RED" "$C_RESET"
    printf '%sNeed at least:%s %d cols × %d rows\n' "$C_YELLOW" "$C_RESET" "$MIN_TERM_COLS" "$MIN_TERM_ROWS"
    printf '%sCurrent size:%s %d cols × %d rows\n' "$C_WHITE" "$C_RESET" "$TERM_COLS" "$TERM_ROWS"
    printf '%sResize terminal to continue. Press q to quit.%s%s' "$C_CYAN" "$C_RESET" "$CLR_EOS"
}

strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
    REPLY="$v"
}

# --- Data Engine ---

register_template() {
    local -i tab_idx="$1"
    local label="$2" toml_key="$3" def_state="$4" check_cmd="${5:-}"

    ITEM_MAP["${tab_idx}::${label}"]="${toml_key}|bool||||"
    DEFAULTS["${tab_idx}::${label}"]="$def_state"
    CHECK_CMDS["${toml_key}"]="$check_cmd"

    local -n _reg_tab_ref="TAB_ITEMS_${tab_idx}"
    _reg_tab_ref+=("$label")
}

populate_config_cache() {
    CONFIG_CACHE=()
    while IFS='=' read -r key val; do
        [[ -z "$key" ]] && continue
        CONFIG_CACHE["$key"]="$val"
    done < <(LC_ALL=C awk '
        /^#?[[:space:]]*\[templates\./ {
            line = $0
            is_commented = match(line, /^#/) ? "false" : "true"
            sub(/^#?[[:space:]]*\[templates\./, "", line)
            sub(/\].*/, "", line)
            print line "=" is_commented
        }
    ' "$CONFIG_FILE")
}

auto_discover_templates() {
    local -a discovered=()
    local toml_key registered_key r_key found

    # Cross-reference TOML blocks against the registered ITEM_MAP
    for toml_key in "${!CONFIG_CACHE[@]}"; do
        found=0
        for registered_key in "${!ITEM_MAP[@]}"; do
            IFS='|' read -r r_key _ <<< "${ITEM_MAP[$registered_key]}"
            if [[ "$r_key" == "$toml_key" ]]; then
                found=1
                break
            fi
        done
        if (( ! found )); then
            discovered+=("$toml_key")
        fi
    done

    # If unmapped blocks exist, spawn the 'Discovered' tab
    if (( ${#discovered[@]} > 0 )); then
        IFS=$'\n' read -r -d '' -a discovered < <(printf '%s\n' "${discovered[@]}" | sort) || true

        local -i disc_tab_idx=${#TABS[@]}
        TABS+=("Discovered")
        TAB_COUNT=${#TABS[@]}
        declare -ga "TAB_ITEMS_${disc_tab_idx}=()"

        for disc_key in "${discovered[@]}"; do
            # Discovered items default to on, and bypass the smart checker
            register_template "$disc_tab_idx" "$disc_key" "$disc_key" "true" ""
        done
    fi
}

write_value_to_file() {
    local toml_key="$1" new_val="$2"
    local current_val="${CONFIG_CACHE["$toml_key"]:-}"

    LAST_WRITE_CHANGED=0
    if [[ -n "${CONFIG_CACHE["$toml_key"]+_}" && "$current_val" == "$new_val" ]]; then
        return 0
    fi

    create_tmpfile || { set_status "Atomic save unavailable."; return 1; }

    TARGET_KEY="$toml_key" NEW_VALUE="$new_val" \
    LC_ALL=C awk '
    function is_blank(line) {
        return line ~ /^[[:space:]]*$/
    }

    function is_comment(line) {
        return line ~ /^[[:space:]]*#/
    }

    function strip_comment_prefix(line,    t) {
        t = line
        sub(/^[[:space:]]*#[[:space:]]?/, "", t)
        return t
    }

    function is_toml_header(line,    t) {
        t = line
        sub(/^[[:space:]]*#?[[:space:]]*/, "", t)
        return t ~ /^\[.*\][[:space:]]*(#.*)?$/
    }

    function template_name(line,    t) {
        t = line
        sub(/^[[:space:]]*#?[[:space:]]*/, "", t)
        if (t !~ /^\[templates\.[^]]+\][[:space:]]*(#.*)?$/) {
            return ""
        }
        sub(/^\[templates\./, "", t)
        sub(/\][[:space:]]*(#.*)?$/, "", t)
        return t
    }

    function count_token(str, tok,    n, p, step, rest) {
        n = 0
        rest = str
        step = length(tok)
        p = index(rest, tok)
        while (p) {
            n++
            rest = substr(rest, p + step)
            p = index(rest, tok)
        }
        return n
    }

    function update_multiline_state(line,    s, c) {
        s = strip_comment_prefix(line)

        if (!in_multiline) {
            c = count_token(s, triple_sq)
            if (c % 2 == 1) {
                in_multiline = 1
                multiline_token = triple_sq
                return
            }

            c = count_token(s, triple_dq)
            if (c % 2 == 1) {
                in_multiline = 1
                multiline_token = triple_dq
            }
            return
        }

        c = count_token(s, multiline_token)
        if (c % 2 == 1) {
            in_multiline = 0
            multiline_token = ""
        }
    }

    {
        lines[++line_count] = $0
    }

    END {
        triple_sq = sprintf("%c%c%c", 39, 39, 39)
        triple_dq = "\"\"\""

        start = 0
        end = line_count
        in_multiline = 0
        multiline_token = ""

        for (i = 1; i <= line_count; i++) {
            if (template_name(lines[i]) == ENVIRON["TARGET_KEY"]) {
                start = i
                break
            }
        }

        if (!start) {
            exit 1
        }

        for (i = start + 1; i <= line_count; i++) {
            if (!in_multiline && is_toml_header(lines[i])) {
                end = i - 1
                break
            }

            if (!in_multiline && is_blank(lines[i]) && i + 3 <= line_count) {
                c1 = lines[i + 1]
                c2 = lines[i + 2]
                c3 = lines[i + 3]

                s1 = strip_comment_prefix(c1)
                s2 = strip_comment_prefix(c2)
                s3 = strip_comment_prefix(c3)

                if (is_comment(c1) && is_comment(c2) && is_comment(c3) &&
                    s1 ~ /^-{3,}$/ &&
                    s2 !~ /^[[:space:]]*$/ &&
                    s2 !~ /^[[:space:]]*\[/ &&
                    s2 !~ /=/ &&
                    s3 ~ /^-{3,}$/) {

                    j = i + 4
                    while (j <= line_count && is_blank(lines[j])) {
                        j++
                    }

                    if (j > line_count || is_toml_header(lines[j])) {
                        end = i - 1
                        break
                    }
                }
            }

            update_multiline_state(lines[i])
        }

        for (i = 1; i <= line_count; i++) {
            line = lines[i]

            if (i >= start && i <= end) {
                if (ENVIRON["NEW_VALUE"] == "true") {
                    sub(/^#[[:space:]]?/, "", line)
                } else if (ENVIRON["NEW_VALUE"] == "false") {
                    if (line !~ /^#/ && line !~ /^[[:space:]]*$/) {
                        line = "# " line
                    }
                }
            }

            print line
        }
    }
    ' "$CONFIG_FILE" > "$_TMPFILE" || {
        rm -f -- "$_TMPFILE" 2>/dev/null || :
        _TMPFILE=""
        _TMPMODE=""
        set_status "Key not found: ${toml_key}"
        return 1
    }

    commit_tmpfile || {
        rm -f -- "$_TMPFILE" 2>/dev/null || :
        _TMPFILE=""
        _TMPMODE=""
        set_status "Atomic save failed."
        return 1
    }

    CONFIG_CACHE["$toml_key"]="$new_val"
    LAST_WRITE_CHANGED=1
    return 0
}

get_active_context() {
    REPLY_CTX="${CURRENT_TAB}"
    REPLY_REF="TAB_ITEMS_${CURRENT_TAB}"
}

load_active_values() {
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _lav_items_ref="$REPLY_REF"
    local item key

    for item in "${_lav_items_ref[@]}"; do
        IFS='|' read -r key _ <<< "${ITEM_MAP["${REPLY_CTX}::${item}"]}"
        if [[ -n "${CONFIG_CACHE["$key"]+_}" ]]; then
            VALUE_CACHE["${REPLY_CTX}::${item}"]="${CONFIG_CACHE["$key"]}"
        else
            VALUE_CACHE["${REPLY_CTX}::${item}"]="$UNSET_MARKER"
        fi
    done
}

modify_value() {
    local label="$1"
    local REPLY_REF REPLY_CTX
    get_active_context

    local key new_val cmd
    IFS='|' read -r key _ <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    local current="${VALUE_CACHE["${REPLY_CTX}::${label}"]:-}"

    if [[ "$current" == "$UNSET_MARKER" || -z "$current" ]]; then
        current="${DEFAULTS["${REPLY_CTX}::${label}"]:-false}"
    fi

    # Toggle Logic with Smart Checks
    if [[ "$current" == "true" ]]; then
        new_val="false"
        clear_status
    else
        new_val="true"
        cmd="${CHECK_CMDS["$key"]:-}"
        if [[ -n "$cmd" ]] && ! command -v "$cmd" &>/dev/null; then
            set_status "Warning: '${cmd}' not installed. Enabled anyway."
        else
            clear_status
        fi
    fi

    if write_value_to_file "$key" "$new_val"; then
        VALUE_CACHE["${REPLY_CTX}::${label}"]="$new_val"
        if (( LAST_WRITE_CHANGED )); then
            post_write_action
        fi
    fi
}

set_absolute_value() {
    local label="$1" new_val="$2"
    local REPLY_REF REPLY_CTX
    get_active_context
    local key
    IFS='|' read -r key _ <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    if write_value_to_file "$key" "$new_val"; then
        VALUE_CACHE["${REPLY_CTX}::${label}"]="$new_val"
        return 0
    fi
    return 1
}

reset_defaults() {
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _rd_items_ref="$REPLY_REF"
    local item def_val
    local -i any_written=0 any_failed=0

    for item in "${_rd_items_ref[@]}"; do
        def_val="${DEFAULTS["${REPLY_CTX}::${item}"]:-}"
        if [[ -n "$def_val" ]]; then
            if set_absolute_value "$item" "$def_val"; then
                (( LAST_WRITE_CHANGED )) && any_written=1
            else
                any_failed=1
            fi
        fi
    done

    (( any_written )) && post_write_action
    (( any_failed )) && set_status "Some defaults were not written." || clear_status
    return 0
}

# --- UI Rendering Engine ---

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0
        SCROLL_OFFSET=0
        _vis_start=0
        _vis_end=0
        return
    fi
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi

    if (( SELECTED_ROW < SCROLL_OFFSET )); then
        SCROLL_OFFSET=$SELECTED_ROW
    elif (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )); then
        SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
    fi

    local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
    if (( max_scroll < 0 )); then max_scroll=0; fi
    if (( SCROLL_OFFSET > max_scroll )); then SCROLL_OFFSET=$max_scroll; fi

    _vis_start=$SCROLL_OFFSET
    _vis_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    if (( _vis_end > count )); then _vis_end=$count; fi
}

render_scroll_indicator() {
    local -n _rsi_buf=$1
    local position="$2"
    local -i count=$3 boundary=$4

    if [[ "$position" == "above" ]]; then
        if (( SCROLL_OFFSET > 0 )); then
            _rsi_buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n'
        else
            _rsi_buf+="${CLR_EOL}"$'\n'
        fi
    else
        if (( count > MAX_DISPLAY_ROWS )); then
            local position_info="[$(( SELECTED_ROW + 1 ))/${count}]"
            if (( boundary < count )); then
                _rsi_buf+="${C_GREY}    ▼ (more below) ${position_info}${CLR_EOL}${C_RESET}"$'\n'
            else
                _rsi_buf+="${C_GREY}                   ${position_info}${CLR_EOL}${C_RESET}"$'\n'
            fi
        else
            _rsi_buf+="${CLR_EOL}"$'\n'
        fi
    fi
}

render_item_list() {
    local -n _ril_buf=$1
    local -n _ril_items=$2
    local _ril_ctx="$3"
    local -i _ril_vs=$4 _ril_ve=$5
    local -i ri max_len=$(( ITEM_PADDING - 1 ))
    local item val display padded_item

    for (( ri = _ril_vs; ri < _ril_ve; ri++ )); do
        item="${_ril_items[ri]}"
        val="${VALUE_CACHE["${_ril_ctx}::${item}"]:-${UNSET_MARKER}}"

        case "$val" in
            true)            display="${C_GREEN}ENABLED${C_RESET}" ;;
            false)           display="${C_GREY}DISABLED${C_RESET}" ;;
            "$UNSET_MARKER") display="${C_YELLOW}⚠ MISSING${C_RESET}" ;;
            *)               display="${C_WHITE}${val}${C_RESET}" ;;
        esac

        if (( ${#item} > ITEM_PADDING )); then
            printf -v padded_item "%-${max_len}s…" "${item:0:max_len}"
        else
            printf -v padded_item "%-${ITEM_PADDING}s" "$item"
        fi

        if (( ri == SELECTED_ROW )); then
            _ril_buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else
            _ril_buf+="    ${padded_item} : ${display}${CLR_EOL}"$'\n'
        fi
    done

    local -i rows_rendered=$(( _ril_ve - _ril_vs ))
    for (( ri = rows_rendered; ri < MAX_DISPLAY_ROWS; ri++ )); do
        _ril_buf+="${CLR_EOL}"$'\n'
    done
}

draw_main_view() {
    local buf="" pad_buf=""
    local -i i current_col=3 zone_start count left_pad right_pad vis_len _vis_start _vis_end

    buf+="${CURSOR_HOME}${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'

    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local -i v_len=${#REPLY}
    vis_len=$(( t_len + v_len + 1 ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    if (( TAB_SCROLL_START > CURRENT_TAB )); then TAB_SCROLL_START=$CURRENT_TAB; fi
    if (( TAB_SCROLL_START < 0 )); then TAB_SCROLL_START=0; fi

    local tab_line
    local -i max_tab_width=$(( BOX_INNER_WIDTH - 6 ))
    LEFT_ARROW_ZONE=""
    RIGHT_ARROW_ZONE=""

    while true; do
        tab_line="${C_MAGENTA}│ "
        current_col=3
        TAB_ZONES=()
        local -i used_len=0

        if (( TAB_SCROLL_START > 0 )); then
            tab_line+="${C_YELLOW}«${C_RESET} "
            LEFT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        else
            tab_line+="  "
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        fi

        for (( i = TAB_SCROLL_START; i < TAB_COUNT; i++ )); do
            local name="${TABS[i]}"
            local display_name="$name"

            local -i tab_name_len=${#name}
            local -i chunk_len=$(( tab_name_len + 4 ))
            local -i reserve=0

            (( i < TAB_COUNT - 1 )) && reserve=2

            if (( used_len + chunk_len + reserve > max_tab_width )); then
                if (( i < CURRENT_TAB || (i == CURRENT_TAB && TAB_SCROLL_START < CURRENT_TAB) )); then
                    TAB_SCROLL_START=$(( TAB_SCROLL_START + 1 ))
                    continue 2
                fi
                if (( i == CURRENT_TAB )); then
                    local -i avail_label=$(( max_tab_width - used_len - reserve - 4 ))
                    (( avail_label < 1 )) && avail_label=1
                    if (( tab_name_len > avail_label )); then
                        if (( avail_label == 1 )); then
                            display_name="…"
                        else
                            display_name="${name:0:avail_label-1}…"
                        fi
                        tab_name_len=${#display_name}
                        chunk_len=$(( tab_name_len + 4 ))
                    fi
                    zone_start=$current_col
                    tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "
                    TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
                    used_len=$(( used_len + chunk_len ))
                    current_col=$(( current_col + chunk_len ))
                    if (( i < TAB_COUNT - 1 )); then
                        tab_line+="${C_YELLOW}» ${C_RESET}"
                        RIGHT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
                        used_len=$(( used_len + 2 ))
                    fi
                    break
                fi
                tab_line+="${C_YELLOW}» ${C_RESET}"
                RIGHT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
                used_len=$(( used_len + 2 ))
                break
            fi

            zone_start=$current_col
            if (( i == CURRENT_TAB )); then
                tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "
            else
                tab_line+="${C_GREY} ${display_name} ${C_MAGENTA}│ "
            fi
            TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
            used_len=$(( used_len + chunk_len ))
            current_col=$(( current_col + chunk_len ))
        done

        local -i pad=$(( BOX_INNER_WIDTH - used_len - 1 ))
        if (( pad > 0 )); then
            printf -v pad_buf '%*s' "$pad" ''
            tab_line+="$pad_buf"
        fi
        tab_line+="${C_MAGENTA}│${C_RESET}"
        break
    done

    buf+="${tab_line}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    local items_var="TAB_ITEMS_${CURRENT_TAB}"
    local -n _draw_items_ref="$items_var"
    count=${#_draw_items_ref[@]}

    compute_scroll_window "$count"
    render_scroll_indicator buf "above" "$count" "$_vis_start"
    render_item_list buf _draw_items_ref "${CURRENT_TAB}" "$_vis_start" "$_vis_end"
    render_scroll_indicator buf "below" "$count" "$_vis_end"

    buf+=$'\n'"${C_CYAN} [Tab] Category  [r] Reset  [←/→ h/l/Enter] Toggle  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    if [[ -n "$STATUS_MESSAGE" ]]; then
        buf+="${C_CYAN} Status: ${C_RED}${STATUS_MESSAGE}${C_RESET}${CLR_EOL}${CLR_EOS}"
    else
        buf+="${C_CYAN} File: ${C_WHITE}${CONFIG_FILE}${C_RESET}${CLR_EOL}${CLR_EOS}"
    fi
    printf '%s' "$buf"
}

draw_ui() {
    update_terminal_size
    if ! terminal_size_ok; then
        draw_small_terminal_notice
        return
    fi
    draw_main_view
}

# --- Input Handling ---

navigate() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _nav_items_ref="$REPLY_REF"
    local -i count=${#_nav_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
}

navigate_page() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _navp_items_ref="$REPLY_REF"
    local -i count=${#_navp_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
}

navigate_end() {
    local -i target=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _nave_items_ref="$REPLY_REF"
    local -i count=${#_nave_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( count - 1 )); fi
}

adjust() {
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _adj_items_ref="$REPLY_REF"
    if (( ${#_adj_items_ref[@]} == 0 )); then return 0; fi
    modify_value "${_adj_items_ref[SELECTED_ROW]}"
}

switch_tab() {
    local -i dir=${1:-1}
    CURRENT_TAB=$(( (CURRENT_TAB + dir + TAB_COUNT) % TAB_COUNT ))
    SELECTED_ROW=0
    SCROLL_OFFSET=0
    load_active_values
}

set_tab() {
    local -i idx=$1
    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        CURRENT_TAB=$idx
        SELECTED_ROW=0
        SCROLL_OFFSET=0
        load_active_values
    fi
}

handle_mouse() {
    local input="$1"
    local -i button x y i start end
    local zone body="${input#'[<'}"

    [[ "$body" == "$input" ]] && return 0
    local terminator="${body: -1}"
    [[ "$terminator" != "M" && "$terminator" != "m" ]] && return 0

    body="${body%[Mm]}"
    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"
    [[ ! "$field1" =~ ^[0-9]+$ || ! "$field2" =~ ^[0-9]+$ || ! "$field3" =~ ^[0-9]+$ ]] && return 0

    button=$field1
    x=$field2
    y=$field3

    (( button == 64 )) && { navigate -1; return 0; }
    (( button == 65 )) && { navigate 1; return 0; }
    [[ "$terminator" != "M" ]] && return 0

    if (( y == TAB_ROW )); then
        if [[ -n "$LEFT_ARROW_ZONE" ]]; then
            start="${LEFT_ARROW_ZONE%%:*}"
            end="${LEFT_ARROW_ZONE##*:}"
            (( x >= start && x <= end )) && { switch_tab -1; return 0; }
        fi
        if [[ -n "$RIGHT_ARROW_ZONE" ]]; then
            start="${RIGHT_ARROW_ZONE%%:*}"
            end="${RIGHT_ARROW_ZONE##*:}"
            (( x >= start && x <= end )) && { switch_tab 1; return 0; }
        fi
        for (( i = 0; i < TAB_COUNT; i++ )); do
            [[ -z "${TAB_ZONES[i]:-}" ]] && continue
            zone="${TAB_ZONES[i]}"
            start="${zone%%:*}"
            end="${zone##*:}"
            (( x >= start && x <= end )) && { set_tab "$(( i + TAB_SCROLL_START ))"; return 0; }
        done
    fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))
        local -n _mouse_items_ref="TAB_ITEMS_${CURRENT_TAB}"
        local -i count=${#_mouse_items_ref[@]}

        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            if (( x > ADJUST_THRESHOLD && button == 0 )); then adjust; fi
        fi
    fi
    return 0
}

read_escape_seq() {
    local -n _esc_out=$1
    _esc_out=""
    local char
    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; then return 1; fi
    _esc_out+="$char"
    if [[ "$char" == '[' || "$char" == 'O' ]]; then
        while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; do
            _esc_out+="$char"
            if [[ "$char" =~ [a-zA-Z~] ]]; then break; fi
        done
    fi
    return 0
}

handle_key_main() {
    local key="$1"
    case "$key" in
        '[Z')                switch_tab -1; return ;;
        '[A'|'OA')           navigate -1; return ;;
        '[B'|'OB')           navigate 1; return ;;
        '[C'|'OC'|'[D'|'OD') adjust; return ;;
        '[5~')               navigate_page -1; return ;;
        '[6~')               navigate_page 1; return ;;
        '[H'|'[1~')          navigate_end 0; return ;;
        '[F'|'[4~')          navigate_end 1; return ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return ;;
    esac

    case "$key" in
        k|K)                    navigate -1 ;;
        j|J)                    navigate 1 ;;
        l|L|h|H)                adjust ;;
        g)                      navigate_end 0 ;;
        G)                      navigate_end 1 ;;
        $'\t')                  switch_tab 1 ;;
        r|R)                    reset_defaults ;;
        ''|$'\n')               adjust ;;
        $'\x7f'|$'\x08'|$'\e\n') adjust ;;
        q|Q|$'\x03')            exit 0 ;;
    esac
}

handle_input_router() {
    local key="$1" escape_seq=""
    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
            [[ "$key" == "" || "$key" == $'\n' ]] && key=$'\e\n'
        else
            key="ESC"
        fi
    fi

    if ! terminal_size_ok; then
        case "$key" in
            q|Q|$'\x03') exit 0 ;;
        esac
        return 0
    fi
    handle_key_main "$key"
}

# --- Autonomous CLI Router ---
run_autonomous_flags() {
    local action="$1"
    local idx_label toml_key check_cmd final_state
    local -i changes=0 failures=0

    for idx_label in "${!ITEM_MAP[@]}"; do
        IFS='|' read -r toml_key _ <<< "${ITEM_MAP[$idx_label]}"

        if [[ "$action" == "--default" ]]; then
            final_state="${DEFAULTS[$idx_label]:-false}"
        elif [[ "$action" == "--smart" ]]; then
            check_cmd="${CHECK_CMDS[$toml_key]:-}"
            if [[ -n "$check_cmd" ]]; then
                if command -v "$check_cmd" &>/dev/null; then
                    final_state="true"
                else
                    final_state="false"
                fi
            else
                final_state="${DEFAULTS[$idx_label]:-false}"
            fi
        fi

        clear_status
        if write_value_to_file "$toml_key" "$final_state"; then
            (( LAST_WRITE_CHANGED )) && changes=1
        else
            log_err "${toml_key}: ${STATUS_MESSAGE:-write failed}"
            failures=1
        fi
    done

    if (( changes && ! failures )); then
        post_write_action
    fi

    if (( failures )); then
        exit 1
    fi

    exit 0
}

main() {
    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5.0+ required"; exit 1; fi

    local _dep
    for _dep in awk realpath mktemp; do
        if ! command -v "$_dep" &>/dev/null; then
            log_err "Missing dependency: ${_dep}"
            exit 1
        fi
    done

    if [[ ! -f "$CONFIG_FILE" ]]; then
        log_err "Config not found: $CONFIG_FILE"
        exit 1
    fi
    if [[ ! -w "$CONFIG_FILE" ]]; then
        log_err "Config not writable: $CONFIG_FILE"
        exit 1
    fi

    resolve_write_target
    populate_config_cache
    register_items
    auto_discover_templates

    # CLI Flags Processing
    case "${1:-}" in
        --default|--smart)
            run_autonomous_flags "$1"
            ;;
        --help|-h)
            printf "Usage: %s [FLAG]\n\n" "$0"
            printf "Options:\n"
            printf "  --default   Autonomously reset all TOML blocks to script defaults.\n"
            printf "  --smart     Autonomously scan system for packages and enable/disable TOML blocks.\n"
            printf "  --help      Show this help menu.\n"
            exit 0
            ;;
    esac

    if [[ ! -t 0 ]]; then
        log_err "TTY required for interactive mode."
        exit 1
    fi

    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    stty -icanon -echo min 1 time 0 2>/dev/null
    UI_ACTIVE=1

    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    load_active_values

    trap 'draw_ui' WINCH

    local key
    while true; do
        draw_ui
        if ! IFS= read -rsn1 key; then continue; fi
        handle_input_router "$key"
    done
}

main "$@"
