#!/usr/bin/env bash
# =============================================================================
# ELITE HYPRLAND FILE MANAGER SWITCHER - PLATINUM EDITION (v6.6)
# =============================================================================
#
# BASED ON: Dusky TUI Engine v3.9.6 (Template Aligned)
# TARGET:   Arch Linux / Hyprland / UWSM / Wayland

# =============================================================================
# HOW TO ADD NEW FILE MANAGERS
# =============================================================================
# 1. Locate the 'FM_CATALOG' array in the USER CONFIGURATION section.
# 2. Add a new line inside the parentheses following this exact syntax:
#    "key|type|desktop_file|display_name"
#
#    - KEY: The string that will be written to $fileManager in your config.
#    - TYPE: '0' for GUI (Direct exec) | '1' for Terminal (Wrapped in terminal).
#    - DESKTOP_FILE: The filename (e.g., dolphin.desktop) for MIME association.
#    - DISPLAY_NAME: The friendly name shown in the TUI menu.
#
# EXAMPLE: To add 'Dolphin', you would add:
#    "dolphin|0|org.kde.dolphin.desktop|Dolphin (KDE)"
#
# NOTE: The script handles all atomic writes, keybind updates, and UI 
# scrolling automatically. No further logic changes are required.
# =============================================================================

set -euo pipefail
shopt -s extglob

# =============================================================================
# ▼ USER CONFIGURATION ▼
# =============================================================================

# Catalog Format: "Key|Type|DesktopFile|DisplayName"
# Type 0 = GUI (exec, uwsm-app $fileManager)
# Type 1 = Terminal (exec, uwsm-app -- $terminal $fileManager)
declare -ra FM_CATALOG=(
    "nemo|0|nemo.desktop|Nemo (GUI)"
    "yazi|1|yazi.desktop|Yazi (Terminal)"
    "thunar|0|thunar.desktop|Thunar (GUI)"
    "dolphin|0|org.kde.dolphin.desktop|Dolphin (GUI)"
    "nautilus|0|org.gnome.Nautilus.desktop|Nautilus (GUI)"
    "pcmanfm|0|pcmanfm.desktop|PCManFM (GUI)"
    "ranger|1|ranger.desktop|Ranger (Terminal)"
    "lf|1|lf.desktop|Lf (Terminal)"
    "superfile|1|superfile.desktop|Superfile (Terminal)"
)

# Paths
declare -r CONF_VARS="${HOME}/.config/hypr/edit_here/source/default_apps.lua"
declare -r CONF_BINDS="${HOME}/.config/hypr/edit_here/source/keybinds.lua"
declare -r STATE_FILE="${HOME}/.config/dusky/settings/filemanager_switch"

# UI Configuration (Template Aligned)
declare -r APP_TITLE="Dusky File Manager"
declare -r APP_VERSION="v6.6 (Omni-Environment)"
declare -ri BOX_INNER_WIDTH=60
declare -ri MAX_DISPLAY_ROWS=10
declare -ri ITEM_PADDING=38  # Width for label column
declare -ri ADJUST_THRESHOLD=38 # Click boundary for applying vs selecting
declare -ri HEADER_ROWS=4
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

# =============================================================================
# ▲ END CONFIGURATION ▲
# =============================================================================

# --- Pre-computed Constants ---
declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" ''
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
declare -r MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h'
declare -r MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l'

declare -r ESC_READ_TIMEOUT=0.10

# --- State Management ---
declare -i SELECTED_ROW=0
declare -i SCROLL_OFFSET=0
declare -i IN_TUI=0
declare CURRENT_FM_KEY="unknown"
declare CURRENT_TERMINAL="unknown"
declare STATUS_MSG=""
declare ORIGINAL_STTY=""

# --- System Helpers ---

log_info() { printf '%s[INFO]%s %s\n' "$C_CYAN" "$C_RESET" "$1"; }
log_err()  { printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; }

# Context-Aware Logging
log_action() {
    local is_error="${1:-0}"
    local msg="$2"
    local t_type="${3:-0}"
    local term="${4:-}"
    
    local full_msg="$msg"
    # Append the detected terminal if it's a CLI app so the user isn't blind
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

cleanup() {
    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "$ORIGINAL_STTY" 2>/dev/null || :
    fi
    printf '\n' 2>/dev/null || :
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- Core Logic: Atomic Writes & Switcher ---

atomic_write() {
    local target="$1"
    local content="$2"
    local tmp_file
    local dir_name
    
    dir_name=$(dirname "$target")
    mkdir -p "$dir_name"
    
    tmp_file=$(mktemp "${target}.tmp.XXXXXXXXXX") || return 1
    
    if ! { printf '%s\n' "$content" > "$tmp_file" && sync "$tmp_file" && mv -f "$tmp_file" "$target"; }; then
        rm -f "$tmp_file"
        return 1
    fi
}

switch_file_manager() {
    local target="$1"
    local t_type="" t_desktop="" t_name="" found=0
    local entry

    for entry in "${FM_CATALOG[@]}"; do
        IFS='|' read -r k t d n <<< "$entry"
        if [[ "$k" == "$target" ]]; then
            t_type="$t"
            t_desktop="$d"
            t_name="$n"
            found=1
            break
        fi
    done

    if [[ $found -eq 0 ]]; then
        log_action 1 "File manager '$target' not found in catalog."
        return 1
    fi

    if [[ ! -f "$CONF_VARS" ]]; then
        log_action 1 "Config not found: $CONF_VARS"
        return 1
    fi

    local new_vars
    new_vars=$(awk -v val="$target" '
        BEGIN { found=0 }
        /^[[:space:]]*(local[[:space:]]+)?fileManager[[:space:]]*=/ {
            idx = index($0, "=")
            prefix = substr($0, 1, idx)
            print prefix " \"" val "\""
            found=1
            next
        }
        { print }
        END { if(!found) print "fileManager = \"" val "\"" }
    ' "$CONF_VARS")
    
    atomic_write "$CONF_VARS" "$new_vars" || { log_action 1 "Failed to write $CONF_VARS"; return 1; }

    if [[ ! -f "$CONF_BINDS" ]]; then
        log_action 1 "Keybinds not found: $CONF_BINDS"
        return 1
    fi

    local exec_cmd
    if [[ "$t_type" == "1" ]]; then
        exec_cmd='terminal .. " " .. fileManager'
    else
        exec_cmd='fileManager'
    fi

    local new_binds
    new_binds=$(awk -v new_cmd="$exec_cmd" '
        { lines[NR] = $0 }
        END {
            found = 0
            for (i = 1; i <= NR; i++) {
                if (lines[i] ~ /description[[:space:]]*=[[:space:]]*"File Manager"/) {
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
                print "-- Auto-generated by FM Switcher"
                print "hl.bind("
                print "    \"SUPER + E\","
                print "    hl.dsp.exec_cmd(" new_cmd "),"
                print "    { description = \"File Manager\" }"
                print ")"
            }
        }
    ' "$CONF_BINDS")
    
    atomic_write "$CONF_BINDS" "$new_binds" || { log_action 1 "Failed to write $CONF_BINDS"; return 1; }

    if command -v xdg-mime &>/dev/null; then
        xdg-mime default "$t_desktop" inode/directory 2>/dev/null || true
    fi

    local legacy_state="false"
    if [[ "$t_type" == "1" ]]; then
        legacy_state="true"
    fi
    
    atomic_write "$STATE_FILE" "$legacy_state" || true
    atomic_write "${STATE_FILE}.smart" "$target" || true

    # 6. Trigger Hyprland Hot-Reload (Omni-Environment Aware)
    # Only execute IPC hot-reload if a live Hyprland session is detected.
    # Prevents phantom processes and sub-shell errors in SSH/TTY environments.
    if [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]] && command -v hyprctl &>/dev/null; then
        hyprctl reload >/dev/null 2>&1 || true
    fi

    detect_environment
    
    log_action 0 "$t_name" "$t_type" "$CURRENT_TERMINAL"
    return 0
}

detect_environment() {
    if [[ -f "$CONF_VARS" ]]; then
        CURRENT_FM_KEY=$(grep -E -m1 '^[[:space:]]*(local[[:space:]]+)?fileManager[[:space:]]*=' "$CONF_VARS" | cut -d'=' -f2 | tr -d ' "' || true)
        CURRENT_FM_KEY="${CURRENT_FM_KEY//[[:space:]]/}"
        
        CURRENT_TERMINAL=$(grep -E -m1 '^[[:space:]]*(local[[:space:]]+)?terminal[[:space:]]*=' "$CONF_VARS" | cut -d'=' -f2 | tr -d ' "' || true)
        CURRENT_TERMINAL="${CURRENT_TERMINAL//[[:space:]]/}"
        
        if [[ -z "$CURRENT_FM_KEY" ]]; then
            CURRENT_FM_KEY="unknown"
        fi
        if [[ -z "$CURRENT_TERMINAL" ]]; then
            CURRENT_TERMINAL="unknown"
        fi
    else
        CURRENT_FM_KEY="unknown"
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
        SELECTED_ROW=0; SCROLL_OFFSET=0; _vis_start=0; _vis_end=0
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
            local info="[$(( SELECTED_ROW + 1 ))/${count}]"
            if (( boundary < count )); then
                _rsi_buf+="${C_GREY}    ▼ (more below) ${info}${CLR_EOL}${C_RESET}"$'\n'
            else
                _rsi_buf+="${C_GREY}                   ${info}${CLR_EOL}${C_RESET}"$'\n'
            fi
        else
            _rsi_buf+="${CLR_EOL}"$'\n'
        fi
    fi
}

draw_ui() {
    local buf="" pad_buf=""
    local -i vis_len left_pad right_pad
    local -i count=${#FM_CATALOG[@]}
    local -i _vis_start _vis_end
    local item k t d n indicator padded_label

    buf+="${CURSOR_HOME}"
    
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'

    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local -i v_len=${#REPLY}
    vis_len=$(( t_len + v_len + 1 ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    # Environment HUD
    local curr_txt="FM: ${CURRENT_FM_KEY}  |  Term: ${CURRENT_TERMINAL}"
    strip_ansi "$curr_txt"; local -i c_len=${#REPLY}
    left_pad=$(( (BOX_INNER_WIDTH - c_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - c_len - left_pad ))
    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_GREY}FM: ${C_GREEN}${CURRENT_FM_KEY}${C_GREY}  |  Term: ${C_YELLOW}${CURRENT_TERMINAL}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'
    
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    compute_scroll_window "$count"
    render_scroll_indicator buf "above" "$count" "$_vis_start"

    for (( i = _vis_start; i < _vis_end; i++ )); do
        IFS='|' read -r k t d n <<< "${FM_CATALOG[$i]}"
        
        if [[ "$k" == "$CURRENT_FM_KEY" ]]; then
            indicator="${C_GREEN}● ACTIVE${C_RESET}"
        else
            indicator="${C_GREY}○${C_RESET}"
        fi

        local max_len=$(( ITEM_PADDING - 1 ))
        if (( ${#n} > ITEM_PADDING )); then
            printf -v padded_label "%-${max_len}s…" "${n:0:max_len}"
        else
            printf -v padded_label "%-${ITEM_PADDING}s" "$n"
        fi

        if (( i == SELECTED_ROW )); then
            buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_label}${C_RESET} ${indicator}${CLR_EOL}"$'\n'
        else
            buf+="    ${C_CYAN}${padded_label}${C_RESET} ${indicator}${CLR_EOL}"$'\n'
        fi
    done

    local -i rows_rendered=$(( _vis_end - _vis_start ))
    for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do
        buf+="${CLR_EOL}"$'\n'
    done

    render_scroll_indicator buf "below" "$count" "$_vis_end"

    if [[ -n "$STATUS_MSG" ]]; then
        buf+="  ${STATUS_MSG}${CLR_EOL}"$'\n'
    else
        buf+="${CLR_EOL}"$'\n'
    fi

    buf+=$'\n'"${C_CYAN} [↑/↓ j/k] Select  [Enter] Apply  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_CYAN} File: ${C_WHITE}split_config${C_RESET}${CLR_EOL}${CLR_EOS}"

    printf '%s' "$buf"
}

# --- Input Handling ---

navigate() {
    local -i dir=$1
    local -i count=${#FM_CATALOG[@]}
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
    STATUS_MSG=""
}

navigate_page() {
    local -i dir=$1
    local -i count=${#FM_CATALOG[@]}
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
    STATUS_MSG=""
}

navigate_end() {
    local -i target=$1
    local -i count=${#FM_CATALOG[@]}
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( count - 1 )); fi
    STATUS_MSG=""
}

handle_mouse() {
    local input="$1"
    local -i button x y
    local body="${input#'[<'}"
    
    if [[ "$body" == "$input" ]]; then return 0; fi
    local terminator="${body: -1}"
    if [[ "$terminator" != "M" && "$terminator" != "m" ]]; then return 0; fi
    body="${body%[Mm]}"
    
    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"
    
    if [[ ! "$field1" =~ ^[0-9]+$ ]]; then return 0; fi
    button=$field1; x=$field2; y=$field3

    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi

    if [[ "$terminator" != "M" ]]; then return 0; fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))
        local -i count=${#FM_CATALOG[@]}
        
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

apply_selection() {
    local k
    IFS='|' read -r k _ <<< "${FM_CATALOG[$SELECTED_ROW]}"
    switch_file_manager "$k"
}

handle_input() {
    local key="$1"
    local escape_seq=""

    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
        else
            key="ESC"
        fi
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
    for (( i = 0; i < ${#FM_CATALOG[@]}; i++ )); do
        IFS='|' read -r k _ <<< "${FM_CATALOG[$i]}"
        if [[ "$k" == "$CURRENT_FM_KEY" ]]; then
            SELECTED_ROW=$i
            break
        fi
    done

    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    stty -icanon -echo min 1 time 0 2>/dev/null
    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"

    trap 'draw_ui' WINCH

    local key
    while true; do
        draw_ui
        if ! IFS= read -rsn1 key; then continue; fi
        handle_input "$key"
    done
}

main() {
    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5+ required."; exit 1; fi

    # Initialize environment on startup so CLI args have context
    detect_environment

    if [[ $# -eq 0 ]]; then
        run_tui
    else
        case "$1" in
            --nemo)   switch_file_manager "nemo" ;;
            --yazi)   switch_file_manager "yazi" ;;
            --thunar) switch_file_manager "thunar" ;;
            --set)
                if [[ -n "${2:-}" ]]; then
                    switch_file_manager "$2"
                else
                    log_err "Usage: --set <name>"
                    exit 1
                fi
                ;;
            --apply-state)
                if [[ -f "${STATE_FILE}.smart" ]]; then
                    switch_file_manager "$(< "${STATE_FILE}.smart")"
                elif [[ -f "$STATE_FILE" ]]; then
                    if grep -q "true" "$STATE_FILE"; then
                        switch_file_manager "yazi"
                    else
                        switch_file_manager "nemo"
                    fi
                else
                    log_info "No state file found."
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
