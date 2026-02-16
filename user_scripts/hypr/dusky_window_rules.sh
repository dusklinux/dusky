#!/usr/bin/env bash
# ==============================================================================
# Purpose: Interactive TUI to generate, copy, and append Hyprland window rules.
#          * Engine: Dusky TUI Engine v3.9.1 (Ported & Fixed)
#          * Core: Window Scanning & Rule Generation Logic
# ==============================================================================

set -euo pipefail
shopt -s extglob
export LC_NUMERIC=C

# --- Configuration ---
declare -r TARGET_FILE="${HOME}/.config/hypr/edit_here/source/window_rules.conf"
declare -r APP_TITLE="Dusky Window Rule Generator"
declare -r APP_VERSION="v4.5 (Engine v3.9.1)"

# Dimensions
declare -ri BOX_WIDTH=100
declare -ri MAX_DISPLAY_ROWS=10
declare -ri PREVIEW_HEIGHT=14
declare -ri HEADER_HEIGHT=3
declare -ri ITEM_PADDING=32

# --- Pre-computed Constants ---
declare _border_buf
printf -v _border_buf '%*s' "$((BOX_WIDTH - 2))" ''
declare -r BORDER_LINE="${_border_buf// /─}"
unset _border_buf

# --- ANSI Constants ---
declare -r ESC=$'\033'  # <--- FIXED: Restored ESC variable for proper interpolation
declare -r C_RESET=$'\033[0m'
declare -r C_CYAN=$'\033[1;36m'
declare -r C_GREEN=$'\033[1;32m'
declare -r C_MAGENTA=$'\033[1;35m'
declare -r C_RED=$'\033[1;31m'
declare -r C_YELLOW=$'\033[1;33m'
declare -r C_WHITE=$'\033[1;37m'
declare -r C_GREY=$'\033[90m'
declare -r C_INVERSE=$'\033[7m'
declare -r C_COMMENT=$'\033[36m'
declare -r C_DIVIDER=$'\033[1;95m'
declare -r CLR_EOL=$'\033[K'
declare -r CLR_EOS=$'\033[J'
declare -r CLR_SCREEN=$'\033[2J'
declare -r CURSOR_HOME=$'\033[H'
declare -r CURSOR_HIDE=$'\033[?25l'
declare -r CURSOR_SHOW=$'\033[?25h'
declare -r MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h'
declare -r MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l'
declare -r ALT_SCREEN_ON=$'\033[?1049h'
declare -r ALT_SCREEN_OFF=$'\033[?1049l'

# Increased timeout for SSH/remote reliability (Template v3.9.1)
declare -r ESC_READ_TIMEOUT=0.10

# --- State ---
declare -a WINDOW_TITLES=()
declare -a WINDOW_CLASSES=()
declare -a GENERATED_RULES=()
declare -i SELECTED_ROW=0
declare -i SCROLL_OFFSET=0
declare -i ITEM_COUNT=0
declare STATUS_MSG=""
declare ORIGINAL_STTY=""

# --- System Helpers ---

log_err() {
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
}

cleanup() {
    printf '%s%s%s%s' "$MOUSE_OFF" "$ALT_SCREEN_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "$ORIGINAL_STTY" 2>/dev/null || :
    fi
    printf '\n' 2>/dev/null || :
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- String Helpers ---

# Robust ANSI stripping using extglob parameter expansion (Template v3.9.1).
# Handles CSI (ESC[...X) correctly including SGR separators.
strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
    REPLY="$v"
}

escape_regex() {
    local s="$1" c
    for c in '\' '.' '[' ']' '*' '^' '$' '(' ')' '+' '?' '{' '}' '|'; do
        s="${s//"$c"/\\$c}"
    done
    printf '%s' "$s"
}

sanitize_name() {
    local input="$1"
    local output="${input//[^[:alnum:]_-]/}"
    printf '%s' "${output:-unnamed}"
}

# --- Core Logic ---

scan_windows() {
    local cmd
    for cmd in jq hyprctl awk wl-copy; do
        if ! command -v "$cmd" &>/dev/null; then
            log_err "Missing dependency: $cmd"
            exit 1
        fi
    done

    # 1. Gather Monitor Data
    declare -A MON_MAP=()
    local m_id m_w m_h m_scale m_x m_y log_w log_h
    while IFS='|' read -r m_id m_w m_h m_scale m_x m_y; do
        [[ -z "$m_id" ]] && continue
        read -r log_w log_h < <(
            awk -v w="$m_w" -v h="$m_h" -v s="${m_scale:-1}" \
                'BEGIN { s = (s == 0) ? 1 : s; printf "%.0f %.0f\n", w/s, h/s }'
        )
        MON_MAP["$m_id"]="$log_w $log_h $m_x $m_y"
    done < <(hyprctl monitors -j | jq -r '.[] | "\(.id)|\(.width)|\(.height)|\(.scale)|\(.x)|\(.y)"')

    if (( ${#MON_MAP[@]} == 0 )); then
        log_err "No monitors found."
        exit 1
    fi

    # 2. Process Clients
    local raw_clients
    raw_clients=$(hyprctl clients -j)

    local title initialClass mon_id w_w w_h w_x w_y w_float w_mapped
    local m_off_x m_off_y
    local r_w r_h r_x r_y local_x local_y
    local safe_class safe_title safe_name rule_block

    while IFS=$'\t' read -r title initialClass mon_id w_w w_h w_x w_y w_float w_mapped; do
        [[ -z "$initialClass" ]] && continue
        [[ "$w_mapped" != "true" ]] && continue
        [[ ! -v MON_MAP["$mon_id"] ]] && continue

        read -r m_w m_h m_off_x m_off_y <<< "${MON_MAP["$mon_id"]}"
        [[ ! "$m_w" =~ ^[1-9][0-9]*$ ]] && continue

        # Calculations
        read -r r_w r_h r_x r_y local_x local_y < <(
            awk -v ww="$w_w" -v wh="$w_h" -v wx="$w_x" -v wy="$w_y" \
                -v mw="$m_w" -v mh="$m_h" -v mx="$m_off_x" -v my="$m_off_y" \
                'BEGIN {
                    lx = wx - mx; ly = wy - my;
                    printf "%.4f %.4f %.4f %.4f %.0f %.0f\n", ww/mw, wh/mh, lx/mw, ly/mh, lx, ly
                }'
        )

        safe_class=$(escape_regex "$initialClass")
        safe_title=$(escape_regex "$title")
        safe_name=$(sanitize_name "$initialClass")

        # Build Block
        rule_block="${C_DIVIDER}# -----------------------------------------------------${C_RESET}"$'\n'
        rule_block+="# ${title}"$'\n'

        rule_block+="${C_GREEN}windowrule {${C_RESET}"$'\n'
        rule_block+="    name = ${safe_name}"$'\n'
        rule_block+="    match:class = ^(${safe_class})$"$'\n'
        rule_block+="    ${C_COMMENT}# match:title = ^(${safe_title})\$${C_RESET}"$'\n'

        rule_block+="    float = on"$'\n'
        rule_block+="    ${C_COMMENT}# pin = on${C_RESET}"$'\n'

        rule_block+="    size = ${w_w} ${w_h}"$'\n'
        rule_block+="    ${C_COMMENT}# size = (monitor_w * ${r_w}) (monitor_h * ${r_h})${C_RESET}"$'\n'

        rule_block+="    move = ${local_x} ${local_y}"$'\n'
        rule_block+="    ${C_COMMENT}# move = (monitor_w * ${r_x}) (monitor_h * ${r_y})${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# move = (monitor_w-window_w-20) (monitor_h-window_h-20)${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# center = on${C_RESET}"$'\n'

        rule_block+=$'\n'"    ${C_COMMENT}# --- Visuals & Effects ---${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# opacity [active] [inactive]${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# opacity = 0.9 0.9${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# animation = popin${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# rounding = 10${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# border_color = rgb(ff0000)${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# no_blur = on${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# no_shadow = on${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# no_dim = on${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# opaque = on${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# dim_around = on${C_RESET}"$'\n'

        rule_block+=$'\n'"    ${C_COMMENT}# --- Placement ---${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# workspace = 2${C_RESET}"$'\n'
        rule_block+="    ${C_COMMENT}# monitor = DP-1${C_RESET}"$'\n'

        rule_block+="${C_GREEN}}${C_RESET}"$'\n'

        WINDOW_TITLES+=("${title:0:60}")
        WINDOW_CLASSES+=("$initialClass")
        GENERATED_RULES+=("$rule_block")

    done < <(printf '%s' "$raw_clients" | jq -r '.[] | [.title, .initialClass, .monitor, .size[0], .size[1], .at[0], .at[1], .floating, .mapped] | @tsv')

    ITEM_COUNT=${#WINDOW_TITLES[@]}
    if (( ITEM_COUNT == 0 )); then
        printf 'No visible windows found.\n'
        exit 0
    fi
}

# --- UI Rendering Engine ---

# Computes scroll window and clamps SELECTED_ROW (Template v3.9.1)
# Sets: SCROLL_OFFSET, SELECTED_ROW, _vis_start, _vis_end
compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0; SCROLL_OFFSET=0
        _vis_start=0; _vis_end=0
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

draw_ui() {
    local buf=""
    local -i i _vis_start _vis_end

    buf+="${CURSOR_HOME}${C_MAGENTA}┌${BORDER_LINE}┐${C_RESET}"$'\n'

    # --- HEADER ---
    local title_str="${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}"
    local -i raw_len=$(( ${#APP_TITLE} + ${#APP_VERSION} + 1 ))
    local -i pad_len=$(( BOX_WIDTH - raw_len - 4 ))
    local padding
    if (( pad_len < 0 )); then pad_len=0; fi
    printf -v padding '%*s' "$pad_len" ""

    # FIXED: Replaced literal $'\033' inside quotes with ${ESC}
    buf+="${C_MAGENTA}│ ${title_str}${padding}${C_RESET}${CLR_EOL}${ESC}[${BOX_WIDTH}G${C_MAGENTA}│${C_RESET}"$'\n'
    buf+="${C_MAGENTA}├${BORDER_LINE}┤${C_RESET}"$'\n'

    # --- SCROLL LOGIC (Template v3.9.1 compute_scroll_window) ---
    compute_scroll_window "$ITEM_COUNT"

    # --- LIST ITEMS ---
    local title class line_content
    for (( i = _vis_start; i < _vis_end; i++ )); do
        title="${WINDOW_TITLES[i]}"
        class="${WINDOW_CLASSES[i]}"

        if (( i == SELECTED_ROW )); then
            line_content="${C_CYAN}➤ ${C_INVERSE} ${class} ${C_RESET}${C_GREY} :: ${C_WHITE}${title}${C_RESET}"
        else
            line_content="   ${C_CYAN}${class} ${C_GREY}:: ${C_WHITE}${title}${C_RESET}"
        fi

        # FIXED: Replaced literal $'\033' inside quotes with ${ESC}
        buf+="${C_MAGENTA}│ ${line_content}${C_RESET}${CLR_EOL}${ESC}[${BOX_WIDTH}G${C_MAGENTA}│${C_RESET}"$'\n'
    done

    # --- EMPTY ROWS ---
    local -i rows_rendered=$(( _vis_end - _vis_start ))
    for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do
        # FIXED: Replaced literal $'\033' inside quotes with ${ESC}
        buf+="${C_MAGENTA}│ ${CLR_EOL}${ESC}[${BOX_WIDTH}G│${C_RESET}"$'\n'
    done

    buf+="${C_MAGENTA}├${BORDER_LINE}┤${C_RESET}"$'\n'
    buf+="${C_MAGENTA}│ ${C_WHITE}PREVIEW:${C_RESET}${CLR_EOL}${ESC}[${BOX_WIDTH}G${C_MAGENTA}│${C_RESET}"$'\n'

    # --- PREVIEW CONTENT ---
    local preview_content="${GENERATED_RULES[$SELECTED_ROW]}"
    local -i line_count=0
    local line

    while IFS= read -r line; do
        (( ++line_count )) || :

        if (( line_count <= PREVIEW_HEIGHT )); then
            strip_ansi "$line"
            local -i clean_len=${#REPLY}
            if (( clean_len > BOX_WIDTH - 4 )); then
                line="${line:0:$((BOX_WIDTH - 6))}.."
            fi
            # FIXED: Replaced literal $'\033' inside quotes with ${ESC}
            buf+="${C_MAGENTA}│ ${line}${C_RESET}${CLR_EOL}${ESC}[${BOX_WIDTH}G${C_MAGENTA}│${C_RESET}"$'\n'
        fi
    done <<< "$preview_content"

    # --- EMPTY PREVIEW ROWS ---
    for (( i = line_count; i < PREVIEW_HEIGHT; i++ )); do
        # FIXED: Replaced literal $'\033' inside quotes with ${ESC}
        buf+="${C_MAGENTA}│ ${CLR_EOL}${ESC}[${BOX_WIDTH}G│${C_RESET}"$'\n'
    done

    buf+="${C_MAGENTA}└${BORDER_LINE}┘${C_RESET}"$'\n'
    buf+="${C_CYAN} [↑/↓] Select  [Enter] Append  [c] Copy  [q] Quit${C_RESET}${CLR_EOL}"$'\n'

    if [[ -n "$STATUS_MSG" ]]; then
        buf+="${C_YELLOW} ${STATUS_MSG}${C_RESET}${CLR_EOL}"$'\n'
    else
        buf+="${C_CYAN} Target: ${C_WHITE}${TARGET_FILE}${C_RESET}${CLR_EOL}"$'\n'
    fi

    buf+="${CLR_EOS}"
    printf '%s' "$buf"
}

# --- Input Handling & Navigation ---

navigate() {
    local -i dir=$1
    (( ITEM_COUNT == 0 )) && return 0
    SELECTED_ROW=$(( (SELECTED_ROW + dir + ITEM_COUNT) % ITEM_COUNT ))
}

navigate_page() {
    local -i dir=$1
    (( ITEM_COUNT == 0 )) && return 0
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= ITEM_COUNT )); then SELECTED_ROW=$(( ITEM_COUNT - 1 )); fi
}

navigate_end() {
    local -i target=$1
    (( ITEM_COUNT == 0 )) && return 0
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( ITEM_COUNT - 1 )); fi
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
    if [[ ! "$field2" =~ ^[0-9]+$ ]]; then return 0; fi
    if [[ ! "$field3" =~ ^[0-9]+$ ]]; then return 0; fi
    button=$field1; x=$field2; y=$field3

    # Scroll wheel (64=up, 65=down)
    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi

    # Only handle Button Press ('M') for clicks
    if [[ "$terminator" != "M" ]]; then return 0; fi

    # Left Click (0)
    if (( button == 0 )); then
        local -i list_start_y=$(( HEADER_HEIGHT + 1 ))
        local -i list_end_y=$(( list_start_y + MAX_DISPLAY_ROWS - 1 ))

        if (( y >= list_start_y && y <= list_end_y )); then
            local -i clicked_idx=$(( y - list_start_y + SCROLL_OFFSET ))
            if (( clicked_idx >= 0 && clicked_idx < ITEM_COUNT )); then
                SELECTED_ROW=$clicked_idx
            fi
        fi
    fi
    return 0
}

# --- Actions ---

get_clean_rule() {
    strip_ansi "${GENERATED_RULES[$SELECTED_ROW]}"
    # REPLY is set by strip_ansi
}

copy_clipboard() {
    get_clean_rule
    if printf '%s\n' "$REPLY" | wl-copy; then
        STATUS_MSG="[SUCCESS] Copied to clipboard!"
    else
        STATUS_MSG="[ERROR] Failed to copy (wl-copy missing?)"
    fi
}

append_selection() {
    get_clean_rule
    local rule_clean="$REPLY"

    if [[ ! -f "$TARGET_FILE" ]]; then
        STATUS_MSG="[ERROR] Target file not found!"
        return
    fi

    if [[ -s "$TARGET_FILE" ]]; then
        local last_char
        last_char=$(tail -c 1 "$TARGET_FILE")
        if [[ -n "$last_char" && "$last_char" != $'\n' ]]; then
            printf '\n' >> "$TARGET_FILE"
        fi
    fi

    printf '%s\n' "$rule_clean" >> "$TARGET_FILE"
    STATUS_MSG="[SUCCESS] Rule appended to config!"
}

# --- Escape Sequence Reader (Template v3.9.1) ---

read_escape_seq() {
    local -n _esc_out=$1
    _esc_out=""
    local char
    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; then
        return 1
    fi
    _esc_out+="$char"
    if [[ "$char" == '[' || "$char" == 'O' ]]; then
        while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; do
            _esc_out+="$char"
            if [[ "$char" =~ [a-zA-Z~] ]]; then break; fi
        done
    fi
    return 0
}

# --- Input Router (Template v3.9.1 pattern) ---

handle_key() {
    local key="$1"
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
        c|C)            copy_clipboard ;;
        ''|$'\n')       append_selection ;;
        q|Q|$'\x03')    exit 0 ;;
    esac
}

handle_input_router() {
    local key="$1"
    local escape_seq=""

    if [[ -n "$STATUS_MSG" && -n "$key" ]]; then STATUS_MSG=""; fi

    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
        else
            # Bare ESC — no action in this context
            return
        fi
    fi

    handle_key "$key"
}

# --- Main ---

main() {
    if (( BASH_VERSINFO[0] < 5 )); then
        log_err "Bash 5.0+ required (found ${BASH_VERSION})"
        exit 1
    fi

    if [[ ! -t 0 ]]; then
        log_err "Interactive terminal (TTY) required on stdin"
        exit 1
    fi

    local _dep
    for _dep in jq hyprctl awk wl-copy; do
        if ! command -v "$_dep" &>/dev/null; then
            log_err "Missing dependency: ${_dep}"
            exit 1
        fi
    done

    scan_windows

    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    stty -icanon -echo min 1 time 0 2>/dev/null

    printf '%s%s%s%s' "$ALT_SCREEN_ON" "$MOUSE_ON" "$CURSOR_HIDE" "$CURSOR_HOME"

    local key
    while true; do
        draw_ui
        IFS= read -rsn1 key || break
        handle_input_router "$key"
    done
}

main "$@"
