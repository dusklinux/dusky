#!/usr/bin/env bash
# =============================================================================
# ELITE HYPRLAND BROWSER SWITCHER - PLATINUM EDITION (v6.8)
# =============================================================================
#
# TARGET:   Arch Linux / Hyprland / UWSM / Wayland
#
# =============================================================================
# HOW TO ADD NEW BROWSERS
# =============================================================================
# 1. Locate the 'BROWSER_CATALOG' array in the USER CONFIGURATION section.
# 2. Add a new line inside the parentheses following this exact syntax:
#    "key|type|desktop_file|display_name"
#
#    - KEY: The string that will be written to $browser in your config.
#    - TYPE: '0' for GUI (Direct exec) | '1' for Terminal (Wrapped in terminal).
#    - DESKTOP_FILE: The filename (e.g., firefox.desktop) for MIME association.
#    - DISPLAY_NAME: The friendly name shown in the TUI menu.
# =============================================================================

set -Eeuo pipefail
shopt -s extglob

# =============================================================================
# ▼ USER CONFIGURATION ▼
# =============================================================================

# Catalog Format: "Key|Type|DesktopFile|DisplayName"
declare -ra BROWSER_CATALOG=(
    "firefox|0|firefox.desktop|Firefox"
    "chromium|0|chromium.desktop|Chromium"
    "zen-browser|0|zen.desktop|Zen Browser"
    "brave|0|brave-browser.desktop|Brave"
    "librewolf|0|librewolf.desktop|LibreWolf"
    "edge|0|microsoft-edge.desktop|Microsoft Edge"
    "vivaldi|0|vivaldi-stable.desktop|Vivaldi"
    "qutebrowser|0|org.qutebrowser.qutebrowser.desktop|Qutebrowser"
    "lynx|1|lynx.desktop|Lynx (Terminal)"
)

# Paths
declare -r CONF_VARS="${HOME}/.config/hypr/edit_here/source/default_apps.lua"
declare -r CONF_BINDS="${HOME}/.config/hypr/edit_here/source/keybinds.lua"
declare -r STATE_FILE="${HOME}/.config/dusky/settings/browser_switch"
declare -r LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/dusky_browser_switch.lock"

# UI Configuration (Template Aligned)
declare -r APP_TITLE="Dusky Browser Switcher"
declare -r APP_VERSION="v6.8 (Armored TUI Engine)"
declare -ri BOX_INNER_WIDTH=60
declare -ri MAX_DISPLAY_ROWS=10
declare -ri ITEM_PADDING=38  # Width for label column
declare -ri ADJUST_THRESHOLD=38 # Click boundary for applying vs selecting
declare -ri HEADER_ROWS=4
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

# Terminal Constraints
declare -ri MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
declare -ri MIN_TERM_ROWS=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 6 ))

# =============================================================================
# ▲ END CONFIGURATION ▲
# =============================================================================

# --- Pre-computed Constants ---
declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" '' || true
declare -r H_LINE="${_h_line_buf// /─}"
unset _h_line_buf

# --- ANSI Constants (Matches Template) ---
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
declare -r ALT_SCREEN_ON=$'\033[?1049h'
declare -r ALT_SCREEN_OFF=$'\033[?1049l'
declare -r MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h'
declare -r MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l'

declare -r ESC_READ_TIMEOUT=0.08
declare -r READ_LOOP_TIMEOUT=0.25

# --- State Management ---
declare -i SELECTED_ROW=0 SCROLL_OFFSET=0 IN_TUI=0
declare -i TERM_ROWS=0 TERM_COLS=0
declare -gi RESIZE_PENDING=0
declare CURRENT_BROWSER_KEY="unknown" CURRENT_TERMINAL="unknown"
declare STATUS_MSG="" ORIGINAL_STTY=""
declare -a _TEMP_PATHS=()

# --- System Helpers ---

log_info() { printf '%s[INFO]%s %s\n' "$C_CYAN" "$C_RESET" "$1"; }
log_err()  { printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; }

register_temp() {
    local path=$1
    [[ -n $path ]] && _TEMP_PATHS+=("$path")
}

forget_temp() {
    local path=$1 kept=() item
    for item in "${_TEMP_PATHS[@]:-}"; do
        [[ $item == "$path" ]] || kept+=("$item")
    done
    _TEMP_PATHS=("${kept[@]:-}")
}

cleanup() {
    local path
    if [[ -t 1 ]] && (( IN_TUI )); then
        printf '%s%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" "$ALT_SCREEN_OFF" 2>/dev/null || :
    fi
    [[ -n ${ORIGINAL_STTY:-} ]] && stty "$ORIGINAL_STTY" < /dev/tty 2>/dev/null || :

    for path in "${_TEMP_PATHS[@]:-}"; do
        [[ -n $path && -e $path ]] && rm -f -- "$path" 2>/dev/null || :
    done
    _TEMP_PATHS=()
    [[ -f $LOCK_FILE ]] && rm -f -- "$LOCK_FILE" 2>/dev/null || :
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 131' QUIT
trap 'exit 143' TERM

release_lock() {
    local fd=${1:-}
    if [[ $fd =~ ^[0-9]+$ ]]; then
        flock -u "$fd" 2>/dev/null || :
        exec {fd}>&- 2>/dev/null || :
    fi
}

log_action() {
    local is_error="${1:-0}" msg="$2" t_type="${3:-0}" term="${4:-}"
    local full_msg="$msg"
    
    if (( is_error == 0 )) && [[ "$t_type" == "1" ]] && [[ -n "$term" && "$term" != "unknown" ]]; then
        full_msg="$msg (via $term)"
    fi

    if (( IN_TUI )); then
        if (( is_error != 0 )); then
            STATUS_MSG="${C_RED}Error: ${msg}${C_RESET}"
        else
            STATUS_MSG="${C_GREEN}Success: Switched to ${full_msg}${C_RESET}"
        fi
    else
        if (( is_error != 0 )); then log_err "$msg"; else log_info "Switched to $full_msg"; fi
    fi
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

terminal_size_ok() {
    (( TERM_COLS >= MIN_TERM_COLS && TERM_ROWS >= MIN_TERM_ROWS ))
}

# --- Core Logic: Atomic Writes & Switcher ---

# Armored atomic write with permission preservation
atomic_write() {
    local target="$1" content="$2" target_dir tmp_file
    target_dir=$(dirname "$target")
    mkdir -p "$target_dir" 2>/dev/null || :
    
    tmp_file=$(mktemp --tmpdir="$target_dir" ".browser_switch.tmp.XXXXXXXXXX") || return 1
    register_temp "$tmp_file"
    
    if ! printf '%s\n' "$content" > "$tmp_file"; then
        rm -f -- "$tmp_file"; forget_temp "$tmp_file"; return 1
    fi
    sync "$tmp_file" 2>/dev/null || :

    if [[ -e $target && -f $target ]]; then
        chown --reference="$target" -- "$tmp_file" 2>/dev/null || :
        chmod --reference="$target" -- "$tmp_file" 2>/dev/null || :
    fi

    if mv -fT -- "$tmp_file" "$target"; then
        forget_temp "$tmp_file"
        return 0
    else
        rm -f -- "$tmp_file"; forget_temp "$tmp_file"
        return 1
    fi
}

switch_browser() {
    local target="$1" t_type="" t_desktop="" t_name="" found=0 lock_fd=""
    local entry new_vars new_binds exec_cmd legacy_state

    # 1. Look up target
    for entry in "${BROWSER_CATALOG[@]}"; do
        IFS='|' read -r k t d n <<< "$entry"
        if [[ "$k" == "$target" ]]; then
            t_type="$t"; t_desktop="$d"; t_name="$n"; found=1
            break
        fi
    done

    if [[ $found -eq 0 ]]; then
        log_action 1 "Browser '$target' not found in catalog."
        return 1
    fi

    # 2. Acquire Process Lock
    mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null || :
    if ! exec {lock_fd}>>"$LOCK_FILE" || ! flock -x -n "$lock_fd"; then
        log_action 1 "Config is locked by another process."
        release_lock "$lock_fd"
        return 1
    fi

    # 3. Mutate Variables
    if [[ ! -f "$CONF_VARS" ]]; then
        log_action 1 "Config not found: $CONF_VARS"
        release_lock "$lock_fd"; return 1
    fi

    new_vars=$(awk -v val="$target" '
        BEGIN { found=0 }
        /^[[:space:]]*(local[[:space:]]+)?browser[[:space:]]*=/ {
            idx = index($0, "=")
            prefix = substr($0, 1, idx)
            print prefix " \"" val "\""
            found=1
            next
        }
        { print }
        END { if(!found) print "browser = \"" val "\"" }
    ' "$CONF_VARS")
    
    atomic_write "$CONF_VARS" "$new_vars" || { log_action 1 "Failed to write $CONF_VARS"; release_lock "$lock_fd"; return 1; }

    # 4. Mutate Keybinds
    if [[ ! -f "$CONF_BINDS" ]]; then
        log_action 1 "Keybinds not found: $CONF_BINDS"
        release_lock "$lock_fd"; return 1
    fi

    if [[ "$t_type" == "1" ]]; then exec_cmd='terminal .. " " .. browser'
    else exec_cmd='browser'; fi

    new_binds=$(awk -v new_cmd="$exec_cmd" '
        { lines[NR] = $0 }
        END {
            found = 0
            for (i = 1; i <= NR; i++) {
                if (lines[i] ~ /description[[:space:]]*=[[:space:]]*"Launch Browser"/) {
                    found = 1
                    for (j = i; j >= 1; j--) {
                        if (lines[j] ~ /hl\.dsp\.exec_cmd/) {
                            sub(/hl\.dsp\.exec_cmd\([^)]+\)/, "hl.dsp.exec_cmd(" new_cmd ")", lines[j])
                            break
                        }
                        if (j < i - 5) break
                    }
                }
            }
            for (i = 1; i <= NR; i++) print lines[i]
            if (!found) {
                print ""
                print "-- Auto-generated by Browser Switcher"
                print "hl.bind("
                print "    \"SUPER + W\","
                print "    hl.dsp.exec_cmd(" new_cmd "),"
                print "    { description = \"Launch Browser\" }"
                print ")"
            }
        }
    ' "$CONF_BINDS")
    
    atomic_write "$CONF_BINDS" "$new_binds" || { log_action 1 "Failed to write $CONF_BINDS"; release_lock "$lock_fd"; return 1; }

    # 5. Handle Mime and State
    if command -v xdg-mime &>/dev/null; then
        xdg-mime default "$t_desktop" x-scheme-handler/http 2>/dev/null || true
        xdg-mime default "$t_desktop" x-scheme-handler/https 2>/dev/null || true
        xdg-mime default "$t_desktop" x-scheme-handler/about 2>/dev/null || true
        xdg-mime default "$t_desktop" x-scheme-handler/unknown 2>/dev/null || true
        xdg-mime default "$t_desktop" text/html 2>/dev/null || true
    fi
    
    if command -v xdg-settings &>/dev/null; then
        xdg-settings set default-web-browser "$t_desktop" 2>/dev/null || true
    fi

    [[ "$target" == "firefox" ]] && legacy_state="true" || legacy_state="false"
    atomic_write "$STATE_FILE" "$legacy_state" || :
    atomic_write "${STATE_FILE}.smart" "$target" || :

    # 6. Release Lock & Reload
    release_lock "$lock_fd"

    if [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]] && command -v hyprctl &>/dev/null; then
        hyprctl reload >/dev/null 2>&1 || true
    fi

    detect_environment
    log_action 0 "$t_name" "$t_type" "$CURRENT_TERMINAL"
    return 0
}

detect_environment() {
    if [[ -f "$CONF_VARS" ]]; then
        CURRENT_BROWSER_KEY=$(grep -E -m1 '^[[:space:]]*(local[[:space:]]+)?browser[[:space:]]*=' "$CONF_VARS" | cut -d'=' -f2 | tr -d ' "' || true)
        CURRENT_TERMINAL=$(grep -E -m1 '^[[:space:]]*(local[[:space:]]+)?terminal[[:space:]]*=' "$CONF_VARS" | cut -d'=' -f2 | tr -d ' "' || true)
        CURRENT_BROWSER_KEY="${CURRENT_BROWSER_KEY:-unknown}"
        CURRENT_TERMINAL="${CURRENT_TERMINAL:-unknown}"
    else
        CURRENT_BROWSER_KEY="unknown"
        CURRENT_TERMINAL="unknown"
    fi
}

# --- UI Rendering Engine ---

strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
    REPLY="$v"
}

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0; SCROLL_OFFSET=0; _vis_start=0; _vis_end=0; return
    fi
    (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
    (( SELECTED_ROW >= count )) && SELECTED_ROW=$(( count - 1 ))
    (( SELECTED_ROW < SCROLL_OFFSET )) && SCROLL_OFFSET=$SELECTED_ROW
    (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )) && SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))

    local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
    (( max_scroll < 0 )) && max_scroll=0
    (( SCROLL_OFFSET > max_scroll )) && SCROLL_OFFSET=$max_scroll

    _vis_start=$SCROLL_OFFSET
    _vis_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    (( _vis_end > count )) && _vis_end=$count
}

render_scroll_indicator() {
    local -n _rsi_buf=$1
    local position="$2"
    local -i count=$3 boundary=$4

    if [[ "$position" == "above" ]]; then
        if (( SCROLL_OFFSET > 0 )); then _rsi_buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n'
        else _rsi_buf+="${CLR_EOL}"$'\n'; fi
    else
        if (( count > MAX_DISPLAY_ROWS )); then
            local info="[$(( SELECTED_ROW + 1 ))/${count}]"
            if (( boundary < count )); then _rsi_buf+="${C_GREY}    ▼ (more below) ${info}${CLR_EOL}${C_RESET}"$'\n'
            else _rsi_buf+="${C_GREY}                   ${info}${CLR_EOL}${C_RESET}"$'\n'; fi
        else _rsi_buf+="${CLR_EOL}"$'\n'; fi
    fi
}

draw_small_terminal_notice() {
    printf '%s%s' "$CURSOR_HOME" "$CLR_SCREEN" || true
    printf '%sTerminal too small%s\n' "$C_RED" "$C_RESET" || true
    printf '%sNeed at least:%s %d cols × %d rows\n' "$C_YELLOW" "$C_RESET" "$MIN_TERM_COLS" "$MIN_TERM_ROWS" || true
    printf '%sCurrent size:%s %d cols × %d rows\n' "$C_WHITE" "$C_RESET" "$TERM_COLS" "$TERM_ROWS" || true
    printf '%sResize terminal to continue. Press q to quit.%s%s' "$C_CYAN" "$C_RESET" "$CLR_EOS" || true
}

draw_ui() {
    update_terminal_size
    if ! terminal_size_ok; then draw_small_terminal_notice; return; fi

    local buf="" pad_buf=""
    local -i vis_len left_pad right_pad
    local -i count=${#BROWSER_CATALOG[@]} _vis_start _vis_end
    local item k t d n indicator padded_label

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

    local curr_txt="Browser: ${CURRENT_BROWSER_KEY}  |  Term: ${CURRENT_TERMINAL}"
    strip_ansi "$curr_txt"; local -i c_len=${#REPLY}
    left_pad=$(( (BOX_INNER_WIDTH - c_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - c_len - left_pad ))
    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_GREY}Browser: ${C_GREEN}${CURRENT_BROWSER_KEY}${C_GREY}  |  Term: ${C_YELLOW}${CURRENT_TERMINAL}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    compute_scroll_window "$count"
    render_scroll_indicator buf "above" "$count" "$_vis_start"

    for (( i = _vis_start; i < _vis_end; i++ )); do
        IFS='|' read -r k t d n <<< "${BROWSER_CATALOG[$i]}"
        if [[ "$k" == "$CURRENT_BROWSER_KEY" ]]; then indicator="${C_GREEN}● ACTIVE${C_RESET}"
        else indicator="${C_GREY}○${C_RESET}"; fi

        local max_len=$(( ITEM_PADDING - 1 ))
        if (( ${#n} > ITEM_PADDING )); then printf -v padded_label "%-${max_len}s…" "${n:0:max_len}"
        else printf -v padded_label "%-${ITEM_PADDING}s" "$n"; fi

        if (( i == SELECTED_ROW )); then buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_label}${C_RESET} ${indicator}${CLR_EOL}"$'\n'
        else buf+="    ${C_CYAN}${padded_label}${C_RESET} ${indicator}${CLR_EOL}"$'\n'; fi
    done

    local -i rows_rendered=$(( _vis_end - _vis_start ))
    for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do buf+="${CLR_EOL}"$'\n'; done
    render_scroll_indicator buf "below" "$count" "$_vis_end"

    if [[ -n "$STATUS_MSG" ]]; then buf+="  ${STATUS_MSG}${CLR_EOL}"$'\n'
    else buf+="${CLR_EOL}"$'\n'; fi

    buf+=$'\n'"${C_CYAN} [↑/↓ j/k] Select  [Enter] Apply  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_CYAN} Target: ${C_WHITE}split_config${C_RESET}${CLR_EOL}${CLR_EOS}"

    printf '%s' "$buf" || true
}

# --- Input Handling ---

navigate() {
    local -i dir=$1 count=${#BROWSER_CATALOG[@]}
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
    STATUS_MSG=""
}

navigate_page() {
    local -i dir=$1 count=${#BROWSER_CATALOG[@]}
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
    STATUS_MSG=""
}

navigate_end() {
    local -i target=$1 count=${#BROWSER_CATALOG[@]}
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( count - 1 )); fi
    STATUS_MSG=""
}

handle_mouse() {
    local input="$1"
    local -i button x y
    local body="${input#'[<'}"
    
    [[ "$body" == "$input" ]] && return 0
    local terminator="${body: -1}"
    [[ "$terminator" != "M" && "$terminator" != "m" ]] && return 0
    body="${body%[Mm]}"
    
    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"
    [[ ! "$field1" =~ ^[0-9]+$ ]] && return 0
    button=$field1; x=$field2; y=$field3

    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi

    [[ "$terminator" != "M" ]] && return 0

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))
        local -i count=${#BROWSER_CATALOG[@]}
        
        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            if (( x > ADJUST_THRESHOLD )); then
                if (( button == 0 )); then apply_selection; fi
            else
                STATUS_MSG=""
            fi
        fi
    fi
}

read_escape_seq() {
    local -n _esc_out=$1
    _esc_out=""
    local char
    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char < /dev/tty; then return 1; fi
    _esc_out+="$char"
    if [[ "$char" == '[' || "$char" == 'O' ]]; then
        while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char < /dev/tty; do
            _esc_out+="$char"
            [[ "$char" =~ [a-zA-Z~] ]] && break
        done
    fi
    return 0
}

apply_selection() {
    local k
    IFS='|' read -r k _ <<< "${BROWSER_CATALOG[$SELECTED_ROW]}"
    switch_browser "$k"
}

handle_input_router() {
    local key="$1" escape_seq=""
    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then key="$escape_seq"
        else key="ESC"; fi
    fi

    if ! terminal_size_ok; then
        case "$key" in q|Q|$'\x03') exit 0 ;; esac
        return 0
    fi

    case "$key" in
        '[A'|'OA')           navigate -1; return ;;
        '[B'|'OB')           navigate 1; return ;;
        '[5~')               navigate_page -1; return ;;
        '[6~')               navigate_page 1; return ;;
        '[H'|'[1~')          navigate_end 0; return ;;
        '[F'|'[4~')          navigate_end 1; return ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return ;;
    esac

    case "$key" in
        k|K)            navigate -1 ;;
        j|J)            navigate 1 ;;
        g)              navigate_end 0 ;;
        G)              navigate_end 1 ;;
        ''|$'\n')       apply_selection ;;
        q|Q|$'\x03')    exit 0 ;;
    esac
}

# --- Main ---

run_tui() {
    if [[ ! -t 0 ]]; then log_err "TUI requires a terminal."; exit 1; fi

    IN_TUI=1
    detect_environment

    local i
    for (( i = 0; i < ${#BROWSER_CATALOG[@]}; i++ )); do
        IFS='|' read -r k _ <<< "${BROWSER_CATALOG[$i]}"
        if [[ "$k" == "$CURRENT_BROWSER_KEY" ]]; then SELECTED_ROW=$i; break; fi
    done

    ORIGINAL_STTY=$(stty -g < /dev/tty 2>/dev/null) || ORIGINAL_STTY=""
    if ! stty -icanon -echo -ixon min 1 time 0 < /dev/tty 2>/dev/null; then 
        log_err "Failed to configure terminal raw input."
        exit 1
    fi
    
    printf '%s%s%s%s%s' "$ALT_SCREEN_ON" "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"

    trap 'RESIZE_PENDING=1' WINCH

    # UI Loop Armor: Drop strict mode to prevent read-timeouts from ghost-crashing
    set +Eeu

    local key
    while true; do
        draw_ui || true
        
        if IFS= read -rsn1 -t "$READ_LOOP_TIMEOUT" key < /dev/tty; then
            (( RESIZE_PENDING )) && RESIZE_PENDING=0
            handle_input_router "$key"
        else
            (( RESIZE_PENDING )) && RESIZE_PENDING=0
        fi
    done
}

main() {
    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5+ required."; exit 1; fi

    detect_environment

    if [[ $# -eq 0 ]]; then
        run_tui
    else
        case "$1" in
            --set)
                if [[ -n "${2:-}" ]]; then switch_browser "$2"
                else log_err "Usage: --set <name>"; exit 1; fi
                ;;
            --apply-state)
                if [[ -f "${STATE_FILE}.smart" ]]; then switch_browser "$(< "${STATE_FILE}.smart")"
                elif [[ -f "$STATE_FILE" ]]; then
                    if grep -q "true" "$STATE_FILE"; then switch_browser "firefox"
                    else switch_browser "chromium"; fi
                else log_info "No state file found."; fi
                ;;
            --*)
                # DYNAMIC CLI PARSER: Automatically supports any key in the BROWSER_CATALOG
                local requested_browser="${1#--}"
                local found=0
                for entry in "${BROWSER_CATALOG[@]}"; do
                    IFS='|' read -r k _ <<< "$entry"
                    if [[ "$k" == "$requested_browser" ]]; then
                        found=1
                        break
                    fi
                done
                
                if (( found )); then
                    switch_browser "$requested_browser"
                else
                    log_err "Unknown browser argument: $1"
                    exit 1
                fi
                ;;
            *)
                log_err "Unknown argument: $1"
                exit 1
                ;;
        esac
    fi
}

main "$@"
