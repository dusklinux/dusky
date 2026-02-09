#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky Matugen Presets v3.7.1 (Hardened & Audited)
# -----------------------------------------------------------------------------
# Target: Arch Linux / Hyprland / Matugen
# Description: High-performance TUI for applying Matugen color schemes.
# -----------------------------------------------------------------------------

set -euo pipefail

# Force standard C locale to prevent decimal format errors (e.g., 0,5 vs 0.5)
export LC_NUMERIC=C

# =============================================================================
# ▼ CONFIGURATION ▼
# =============================================================================

readonly APP_TITLE="Dusky Matugen Presets"
readonly APP_VERSION="v3.7.1"

# --- State Management ---
readonly USE_STATE_FILE=false
readonly STATE_FILE="${HOME}/.config/dusky/settings/dusky_theme/state.conf"

# Dimensions & Layout
declare -ri MAX_DISPLAY_ROWS=16
declare -ri BOX_INNER_WIDTH=80
declare -ri ITEM_PADDING=30
declare -ri ADJUST_THRESHOLD=40

# Minimum terminal dimensions
declare -ri MIN_COLS=82
declare -ri MIN_ROWS=24

# UI Row Calculations
# Structure: 1:Top, 2:Title, 3:Status, 4:Tabs, 5:Bottom
declare -ri HEADER_LINES=5
declare -ri TAB_ROW=4
declare -ri ITEM_START_Y=$(( HEADER_LINES + 1 ))

# Tabs
declare -ra TABS=("Vibrant" "Neon" "Deep" "Pastel" "Mono" "Custom" "Settings")

# Global Settings (Defaults)
declare -A SETTINGS=(
    ["type"]="scheme-fidelity"
    ["mode"]="dark"
    ["contrast"]="0.0"
)

# =============================================================================
# ▼ ANSI CONSTANTS ▼
# =============================================================================

readonly C_RESET=$'\033[0m'
readonly C_CYAN=$'\033[1;36m'
readonly C_GREEN=$'\033[1;32m'
readonly C_MAGENTA=$'\033[1;35m'
readonly C_RED=$'\033[1;31m'
readonly C_YELLOW=$'\033[1;33m'
readonly C_WHITE=$'\033[1;37m'
readonly C_GREY=$'\033[1;30m'
readonly C_INVERSE=$'\033[7m'

readonly CLR_EOL=$'\033[K'
readonly CLR_SCREEN=$'\033[2J'

readonly CURSOR_HOME=$'\033[H'
readonly CURSOR_HIDE=$'\033[?25l'
readonly CURSOR_SHOW=$'\033[?25h'

readonly MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h'
readonly MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l'

# Timeout for reading escape sequences (seconds).
readonly ESC_READ_TIMEOUT=0.05

# =============================================================================
# ▼ DATA REGISTRATION ▼
# =============================================================================

declare -A ITEM_MAP=()
declare -a TAB_ITEMS_0=() TAB_ITEMS_1=() TAB_ITEMS_2=() TAB_ITEMS_3=()
declare -a TAB_ITEMS_4=() TAB_ITEMS_5=() TAB_ITEMS_6=()

register() {
    if (( $# != 3 )); then
        printf '%s[BUG]%s register() requires 3 args, got %d\n' \
            "$C_RED" "$C_RESET" "$#" >&2
        return 1
    fi

    local -i tab_idx=$1
    local label=$2 value=$3

    if (( tab_idx < 0 || tab_idx > 6 )); then
        printf '%s[BUG]%s register() tab_idx %d out of range [0-6]\n' \
            "$C_RED" "$C_RESET" "$tab_idx" >&2
        return 1
    fi

    ITEM_MAP["${tab_idx}::${label}"]="${value}"

    local -n _reg_ref="TAB_ITEMS_${tab_idx}"
    _reg_ref+=("${label}")
}

# --- TAB 0: VIBRANT ---
register 0 "Hyper Red"         "#FF0000"
register 0 "Electric Blue"     "#0000FF"
register 0 "Toxic Green"       "#00FF00"
register 0 "Pure Magenta"      "#FF00FF"
register 0 "Cyan Punch"        "#00FFFF"
register 0 "Safety Yellow"     "#FFFF00"
register 0 "Blood Orange"      "#FF4500"
register 0 "Plasma Purple"     "#6A0DAD"
register 0 "Deep Pink"         "#FF1493"
register 0 "Ultramarine"       "#120A8F"
register 0 "Emerald City"      "#50C878"
register 0 "Crimson Tide"      "#DC143C"
register 0 "Chartreuse"        "#7FFF00"
register 0 "Spring Green"      "#00FF7F"
register 0 "Azure Sky"         "#007FFF"
register 0 "Violet Ray"        "#EE82EE"
register 0 "Aquamarine"        "#7FFFD4"
register 0 "Solid Gold"        "#FFD700"
register 0 "Rich Teal"         "#008080"
register 0 "Olive Drab"        "#808000"

# --- TAB 1: NEON / CYBER ---
register 1 "Laser Lemon"       "#FFFF66"
register 1 "Hot Pink"          "#FF69B4"
register 1 "Cyber Grape"       "#58427C"
register 1 "Neon Carrot"       "#FFA343"
register 1 "Matrix Green"      "#03A062"
register 1 "Electric Indigo"   "#6F00FF"
register 1 "Miami Pink"        "#FF5AC4"
register 1 "Vice Blue"         "#00C6FF"
register 1 "Radioactive"       "#CCFF00"
register 1 "Plastic Purple"    "#D400FF"
register 1 "Arcade Red"        "#FF0055"
register 1 "Hacker Green"      "#00FF2A"
register 1 "Synthwave Sun"     "#FF7E00"
register 1 "Tron Cyan"         "#6EFFFF"
register 1 "Flux Capacitor"    "#FFAE00"
register 1 "Highlighter Blue"  "#1F51FF"
register 1 "Shocking Pink"     "#FC0FC0"
register 1 "Lime Light"        "#BFFF00"

# --- TAB 2: DEEP / DARK ---
register 2 "Midnight Blue"     "#191970"
register 2 "Dark Slate"        "#2F4F4F"
register 2 "Saddle Brown"      "#8B4513"
register 2 "Dark Olive"        "#556B2F"
register 2 "Indigo Dye"        "#4B0082"
register 2 "Maroon"            "#800000"
register 2 "Navy"              "#000080"
register 2 "Dark Green"        "#006400"
register 2 "Dark Cyan"         "#008B8B"
register 2 "Dark Magenta"      "#8B008B"
register 2 "Tyrian Purple"     "#66023C"
register 2 "Oxblood"           "#4A0404"
register 2 "Deep Forest"       "#013220"
register 2 "Night Sky"         "#0C090A"
register 2 "Black Cherry"      "#540026"
register 2 "Deep Coffee"       "#3B2F2F"

# --- TAB 3: PASTEL ---
register 3 "Baby Blue"         "#89CFF0"
register 3 "Mint Cream"        "#F5FFFA"
register 3 "Lavender"          "#E6E6FA"
register 3 "Peach Puff"        "#FFDAB9"
register 3 "Misty Rose"        "#FFE4E1"
register 3 "Honeydew"          "#F0FFF0"
register 3 "Alice Blue"        "#F0F8FF"
register 3 "Lemon Chiffon"     "#FFFACD"
register 3 "Tea Green"         "#D0F0C0"
register 3 "Celeste"           "#B2FFFF"
register 3 "Mauve"             "#E0B0FF"
register 3 "Salmon"            "#FA8072"
register 3 "Cornflower"        "#6495ED"
register 3 "Thistle"           "#D8BFD8"
register 3 "Wheat"             "#F5DEB3"

# --- TAB 4: MONOCHROME ---
register 4 "Pure Black"        "#000000"
register 4 "Pure White"        "#FFFFFF"
register 4 "Dim Gray"          "#696969"
register 4 "Slate Gray"        "#708090"
register 4 "Light Slate"       "#778899"
register 4 "Silver"            "#C0C0C0"
register 4 "Gainsboro"         "#DCDCDC"
register 4 "Charcoal"          "#36454F"
register 4 "Onyx"              "#353839"
register 4 "Gunmetal"          "#2A3439"

# --- TAB 5: CUSTOM INPUT ---
register 5 "Input HEX Code"    "ACTION_INPUT_HEX"
register 5 "Input RGB Values"  "ACTION_INPUT_RGB"
register 5 "Regenerate Last"   "ACTION_REGEN"

# --- TAB 6: SETTINGS ---
register 6 "Scheme Type"       "type|cycle|scheme-fidelity,scheme-content,scheme-fruit-salad,scheme-rainbow,scheme-neutral,scheme-tonal-spot,scheme-expressive,scheme-monochrome"
register 6 "Mode"              "mode|cycle|dark,light"
register 6 "Contrast"          "contrast|float|-1.0|1.0|0.1"

# =============================================================================
# ▼ STATE MANAGEMENT ▼
# =============================================================================

declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=0
declare -i SCROLL_OFFSET=0
declare -ri TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare ORIGINAL_STTY=""

# Initialized to empty so nothing shows as ACTIVE on startup
declare LAST_APPLIED_HEX=""
declare LAST_STATUS_MSG=""

load_state() {
    [[ "${USE_STATE_FILE}" != "true" ]] && return 0
    [[ ! -f "${STATE_FILE}" ]] && return 0

    local key value
    while IFS='=' read -r key value; do
        [[ "${key}" == \#* ]] && continue
        
        value="${value//$'\n'/}"
        value="${value//\"/}"

        case "${key}" in
            THEME_MODE)       [[ -n "${value}" ]] && SETTINGS["mode"]="${value}" ;;
            MATUGEN_TYPE)     [[ -n "${value}" ]] && SETTINGS["type"]="${value}" ;;
            MATUGEN_CONTRAST)
                if [[ -n "${value}" ]]; then
                    if [[ "${value}" == "disable" ]]; then
                        SETTINGS["contrast"]="0.0"
                    else
                        SETTINGS["contrast"]="${value}"
                    fi
                fi
                ;;
        esac
    done < "${STATE_FILE}"
}

save_state() {
    [[ "${USE_STATE_FILE}" != "true" ]] && return 0

    local dir="${STATE_FILE%/*}"
    mkdir -p "${dir}"

    local contrast_val="${SETTINGS["contrast"]}"
    if [[ "${contrast_val}" == "0.0" || "${contrast_val}" == "0" ]]; then
        contrast_val="disable"
    fi

    # Atomic write to prevent corruption
    local tmpfile
    tmpfile=$(mktemp "${dir}/state.XXXXXX") || {
        log_err "Failed to create temp file for state save"
        return 1
    }

    # Clean up temp file if something goes wrong before mv
    trap 'rm -f "${tmpfile}" 2>/dev/null || :' RETURN

    printf '# Dusky Theme State File\nTHEME_MODE=%s\nMATUGEN_TYPE=%s\nMATUGEN_CONTRAST=%s\n' \
        "${SETTINGS["mode"]}" \
        "${SETTINGS["type"]}" \
        "${contrast_val}" > "${tmpfile}"

    mv -f "${tmpfile}" "${STATE_FILE}"
    
    # Clear local trap
    trap - RETURN
}

# =============================================================================
# ▼ CORE LOGIC ▼
# =============================================================================

log_err() {
    printf '%s[ERROR]%s %s\n' "${C_RED}" "${C_RESET}" "$1" >&2
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

enter_raw_mode() {
    stty -icanon -echo min 1 time 0 2>/dev/null || :
    printf '%s%s' "${CURSOR_HIDE}" "${MOUSE_ON}"
}

apply_matugen() {
    local hex="${1^^}"
    local type="${SETTINGS["type"]}"
    local mode="${SETTINGS["mode"]}"
    local contrast="${SETTINGS["contrast"]}"

    LAST_APPLIED_HEX="${hex}"
    save_state

    if matugen color hex "${hex}" \
        --type "${type}" \
        --mode "${mode}" \
        --contrast "${contrast}" >/dev/null 2>&1; then
        LAST_STATUS_MSG="${C_GREEN}✓ Applied: ${hex} (${type})${C_RESET}"
    else
        LAST_STATUS_MSG="${C_RED}✗ Failed to apply: ${hex}${C_RESET}"
    fi
}

prompt_input() {
    local prompt_text=$1
    local -n _prompt_out=$2

    # Switch to cooked mode for typing; disable mouse to prevent stray clicks
    printf '%s%s' "${MOUSE_OFF}" "${CURSOR_SHOW}"
    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "${ORIGINAL_STTY}" 2>/dev/null || stty sane
    else
        stty sane
    fi

    printf '%s%s%s➤ %s%s ' "${C_RESET}" "${CLR_SCREEN}" "${C_CYAN}" "${prompt_text}" "${C_RESET}"

    _prompt_out=""
    read -r _prompt_out || :

    # Restore functionality
    enter_raw_mode
}

validate_hex() {
    [[ $1 =~ ^#?[a-fA-F0-9]{6}$ ]]
}

validate_rgb_component() {
    [[ -n "${1:-}" && $1 =~ ^[0-9]+$ ]] && (( 10#$1 >= 0 && 10#$1 <= 255 ))
}

modify_setting() {
    local label=$1
    local -i direction=$2
    local config="${ITEM_MAP["6::${label}"]}"
    local key type rest

    key="${config%%|*}"
    rest="${config#*|}"
    type="${rest%%|*}"
    rest="${rest#*|}"

    local current="${SETTINGS[${key}]}"
    local new_val=""

    case "${type}" in
        cycle)
            local -a opts=()
            IFS=',' read -r -a opts <<< "${rest}"
            local -i count=${#opts[@]} idx=0 i

            for (( i = 0; i < count; i++ )); do
                if [[ "${opts[i]}" == "${current}" ]]; then
                    idx=$i
                    break
                fi
            done

            # Added explicit parens for modulo safety
            idx=$(( ((idx + direction) % count + count) % count ))
            new_val="${opts[idx]}"
            ;;
        float)
            local s_min s_max s_step
            IFS='|' read -r s_min s_max s_step <<< "${rest}"

            new_val=$(awk \
                -v c="${current}" \
                -v dir="${direction}" \
                -v step="${s_step}" \
                -v lo="${s_min}" \
                -v hi="${s_max}" \
                'BEGIN {
                    val = c + (dir * step)
                    if (val < lo) val = lo
                    if (val > hi) val = hi
                    printf "%.1f", val
                }')
            ;;
        *)
            return 0
            ;;
    esac

    SETTINGS["${key}"]="${new_val}"
    save_state
}

trigger_action() {
    local label=$1
    local val="${ITEM_MAP["${CURRENT_TAB}::${label}"]}"

    if (( CURRENT_TAB == 6 )); then
        modify_setting "${label}" 1
        return 0
    fi

    case "${val}" in
        ACTION_INPUT_HEX)
            local input_hex=""
            prompt_input "Enter HEX (e.g. #FF0000):" input_hex
            if validate_hex "${input_hex}"; then
                [[ "${input_hex}" != \#* ]] && input_hex="#${input_hex}"
                apply_matugen "${input_hex}"
            else
                LAST_STATUS_MSG="${C_RED}Invalid HEX code${C_RESET}"
            fi
            ;;
        ACTION_INPUT_RGB)
            local rgb_str="" r="" g="" b=""
            prompt_input "Enter RGB (e.g. 255 0 0):" rgb_str
            read -r r g b _ <<< "${rgb_str}"

            if validate_rgb_component "${r:-}" \
                && validate_rgb_component "${g:-}" \
                && validate_rgb_component "${b:-}"; then
                local hex
                printf -v hex '#%02x%02x%02x' "$(( 10#${r} ))" "$(( 10#${g} ))" "$(( 10#${b} ))"
                apply_matugen "${hex}"
            else
                LAST_STATUS_MSG="${C_RED}Invalid RGB values${C_RESET}"
            fi
            ;;
        ACTION_REGEN)
            if [[ -z "${LAST_APPLIED_HEX}" ]]; then
                LAST_STATUS_MSG="${C_YELLOW}No color has been applied yet${C_RESET}"
            else
                apply_matugen "${LAST_APPLIED_HEX}"
            fi
            ;;
        '#'*)
            apply_matugen "${val}"
            ;;
    esac
}

# =============================================================================
# ▼ UI RENDERING ▼
# =============================================================================

draw_ui() {
    local buf="" pad_buf="" padded_item="" item val display
    local -i i current_col=3 zone_start len count pad_needed
    local -i visible_len left_pad right_pad
    local -i visible_start visible_end

    # Optimization: hoist local vars out of loop
    local dot="" key="" setting_val=""
    local -i cr=0 cg=0 cb=0

    buf+="${CURSOR_HOME}"

    # --- Top Border ---
    printf -v pad_buf '%*s' "${BOX_INNER_WIDTH}" ''
    buf+="${C_MAGENTA}┌${pad_buf// /─}┐${C_RESET}"$'\n'

    # --- Header ---
    visible_len=$(( ${#APP_TITLE} + ${#APP_VERSION} + 1 ))
    left_pad=$(( (BOX_INNER_WIDTH - visible_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - visible_len - left_pad ))

    printf -v pad_buf '%*s' "${left_pad}" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "${right_pad}" ''
    buf+="${pad_buf}│${C_RESET}"$'\n'

    # --- Status Line ---
    local status_line="${C_MAGENTA}Mode: ${C_CYAN}${SETTINGS[mode]} ${C_MAGENTA}| Type: ${C_CYAN}${SETTINGS[type]} ${C_MAGENTA}| Contrast: ${C_CYAN}${SETTINGS[contrast]}${C_RESET}"
    local raw_status="Mode: ${SETTINGS[mode]} | Type: ${SETTINGS[type]} | Contrast: ${SETTINGS[contrast]}"
    local -i raw_len=${#raw_status}

    if (( raw_len > BOX_INNER_WIDTH - 2 )); then raw_len=$(( BOX_INNER_WIDTH - 2 )); fi
    left_pad=$(( (BOX_INNER_WIDTH - raw_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - raw_len - left_pad ))

    printf -v pad_buf '%*s' "${left_pad}" ''
    buf+="${C_MAGENTA}│${pad_buf}${status_line}"
    printf -v pad_buf '%*s' "${right_pad}" ''
    buf+="${pad_buf}${C_MAGENTA}│${C_RESET}"$'\n'

    # --- Tab Bar ---
    local tab_line="${C_MAGENTA}│ "
    TAB_ZONES=()

    for (( i = 0; i < TAB_COUNT; i++ )); do
        local name="${TABS[i]}"
        len=${#name}
        zone_start=$current_col

        if (( i == CURRENT_TAB )); then
            tab_line+="${C_CYAN}${C_INVERSE} ${name} ${C_RESET}${C_MAGENTA}│ "
        else
            tab_line+="${C_MAGENTA} ${name} ${C_MAGENTA}│ "
        fi

        TAB_ZONES+=("${zone_start}:$(( zone_start + len + 1 ))")
        (( current_col += len + 4 )) || true
    done

    pad_needed=$(( BOX_INNER_WIDTH - current_col + 2 ))
    if (( pad_needed > 0 )); then
        printf -v pad_buf '%*s' "${pad_needed}" ''
        tab_line+="${pad_buf}"
    fi
    tab_line+="${C_MAGENTA}│${C_RESET}"

    buf+="${tab_line}"$'\n'

    # --- Bottom Border ---
    printf -v pad_buf '%*s' "${BOX_INNER_WIDTH}" ''
    buf+="${C_MAGENTA}└${pad_buf// /─}┘${C_RESET}"$'\n'

    # --- Item List Logic ---
    local -n _draw_ref="TAB_ITEMS_${CURRENT_TAB}"
    count=${#_draw_ref[@]}

    # Scroll Math
    if (( count == 0 )); then
        SELECTED_ROW=0; SCROLL_OFFSET=0
    else
        (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
        (( SELECTED_ROW >= count )) && SELECTED_ROW=$(( count - 1 ))

        if (( SELECTED_ROW < SCROLL_OFFSET )); then
            SCROLL_OFFSET=${SELECTED_ROW}
        elif (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )); then
            SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
        fi

        local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
        (( max_scroll < 0 )) && max_scroll=0
        (( SCROLL_OFFSET > max_scroll )) && SCROLL_OFFSET=${max_scroll}
    fi

    visible_start=${SCROLL_OFFSET}
    visible_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    (( visible_end > count )) && visible_end=${count}

    # Render List Items
    for (( i = visible_start; i < visible_end; i++ )); do
        item="${_draw_ref[i]}"
        val="${ITEM_MAP["${CURRENT_TAB}::${item}"]}"

        if (( CURRENT_TAB == 6 )); then
            key="${val%%|*}"
            setting_val="${SETTINGS[${key}]}"
            display="${C_YELLOW}◀ ${setting_val} ▶${C_RESET}"
        elif (( CURRENT_TAB == 5 )); then
            if [[ "${val}" == "ACTION_REGEN" ]]; then
                display="${C_YELLOW}[Enter] to Run${C_RESET}"
            else
                display="${C_CYAN}[Enter] to Type${C_RESET}"
            fi
        else
            # Color Tabs: Render TrueColor preview dot
            dot=""
            if [[ "${val}" =~ ^#?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$ ]]; then
                cr=$(( 16#${BASH_REMATCH[1]} ))
                cg=$(( 16#${BASH_REMATCH[2]} ))
                cb=$(( 16#${BASH_REMATCH[3]} ))
                printf -v dot '\033[38;2;%d;%d;%dm●\033[0m' "${cr}" "${cg}" "${cb}"
            fi

            if [[ -n "${LAST_APPLIED_HEX}" && "${val^^}" == "${LAST_APPLIED_HEX^^}" ]]; then
                display="${dot} ${C_GREEN}ACTIVE${C_RESET}"
            else
                display="${dot} ${C_GREY}${val}${C_RESET}"
            fi
        fi

        printf -v padded_item "%-${ITEM_PADDING}s" "${item:0:${ITEM_PADDING}}"

        if (( i == SELECTED_ROW )); then
            buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else
            buf+="    ${padded_item} : ${display}${CLR_EOL}"$'\n'
        fi
    done

    # Pad Empty Rows
    local -i rows_rendered=$(( visible_end - visible_start ))
    for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do
        buf+="${CLR_EOL}"$'\n'
    done

    # Feedback Line
    if [[ -n "${LAST_STATUS_MSG}" ]]; then
        buf+=$'\n'" ${LAST_STATUS_MSG}${CLR_EOL}"$'\n'
    else
        buf+=$'\n'"${CLR_EOL}"$'\n'
    fi

    # Footer
    buf+="${C_CYAN} [Enter] Apply  [Tab] Switch Tab  [Arrows] Nav  [q] Quit${C_RESET}${CLR_EOL}"$'\n'

    printf '%s' "${buf}"
}

# =============================================================================
# ▼ INPUT HANDLING ▼
# =============================================================================

navigate() {
    local -i dir=$1
    local -n _nav_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_nav_ref[@]}

    (( count == 0 )) && return 0
    
    SELECTED_ROW=$(( SELECTED_ROW + dir ))

    if (( SELECTED_ROW < 0 )); then
        SELECTED_ROW=$(( count - 1 ))
    elif (( SELECTED_ROW >= count )); then
        SELECTED_ROW=0
    fi
    return 0
}

switch_tab() {
    local -i dir=${1:-1}
    CURRENT_TAB=$(( CURRENT_TAB + dir ))

    if (( CURRENT_TAB >= TAB_COUNT )); then
        CURRENT_TAB=0
    elif (( CURRENT_TAB < 0 )); then
        CURRENT_TAB=$(( TAB_COUNT - 1 ))
    fi
    SELECTED_ROW=0
    SCROLL_OFFSET=0
}

set_tab() {
    local -i idx=$1
    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        CURRENT_TAB=$idx
        SELECTED_ROW=0
        SCROLL_OFFSET=0
    fi
}

handle_enter() {
    local -n _act_ref="TAB_ITEMS_${CURRENT_TAB}"
    (( ${#_act_ref[@]} == 0 )) && return 0
    trigger_action "${_act_ref[${SELECTED_ROW}]}"
}

adjust_setting() {
    local -i dir=$1
    (( CURRENT_TAB != 6 )) && return 0
    local -n _adj_ref="TAB_ITEMS_${CURRENT_TAB}"
    (( ${#_adj_ref[@]} == 0 )) && return 0
    modify_setting "${_adj_ref[${SELECTED_ROW}]}" "${dir}"
}

handle_mouse() {
    local input=$1
    local -i button x y i start end
    local type zone

    # SGR mouse: input is like [<0;45;3M or [<64;10;5m
    local body=${input#'[<'}
    [[ "$body" == "$input" ]] && return 0  # No [< prefix found

    local terminator=${body: -1}
    [[ "$terminator" != "M" && "$terminator" != "m" ]] && return 0

    body=${body%[Mm]}
    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"

    # Validate all fields are numeric
    [[ ! "$field1" =~ ^[0-9]+$ ]] && return 0
    [[ ! "$field2" =~ ^[0-9]+$ ]] && return 0
    [[ ! "$field3" =~ ^[0-9]+$ ]] && return 0

    button=$field1
    x=$field2
    y=$field3

    # Scroll wheel
    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi

    # Only process press events (M = press, m = release)
    [[ "$terminator" != "M" ]] && return 0

    # Tab bar clicks (using calculated TAB_ROW)
    if (( y == TAB_ROW )); then
        for (( i = 0; i < TAB_COUNT; i++ )); do
            zone=${TAB_ZONES[i]}
            start=${zone%%:*}
            end=${zone##*:}
            if (( x >= start && x <= end )); then set_tab "$i"; return 0; fi
        done
        return 0
    fi

    # Item area click
    local -n _mouse_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_mouse_items_ref[@]}

    if (( y >= ITEM_START_Y && y < ITEM_START_Y + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - ITEM_START_Y + SCROLL_OFFSET ))
        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            
            if (( CURRENT_TAB == 6 && x > ADJUST_THRESHOLD )); then
                if (( button == 0 )); then adjust_setting 1; else adjust_setting -1; fi
            else
                trigger_action "${_mouse_items_ref[${clicked_idx}]}"
            fi
        fi
    fi
    return 0
}

# Reads escape sequence characters until a valid terminator is found.
read_escape_seq() {
    local -n _esc_out=$1
    local char
    _esc_out=""

    while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; do
        _esc_out+="$char"
        case "$_esc_out" in
            '[Z')              return 0 ;; # Shift-Tab
            O[A-Za-z])         return 0 ;; # SS3 sequences
            '['*[A-Za-z~])     return 0 ;; # CSI sequences (arrows, mouse, function keys)
        esac
    done
    return 0
}

# =============================================================================
# ▼ MAIN LOOP ▼
# =============================================================================

main() {
    # Check dependencies (removed sed, as it is unused)
    local dep
    for dep in awk matugen; do
        if ! command -v "${dep}" &>/dev/null; then
            log_err "Required dependency not found: ${dep}"
            exit 1
        fi
    done

    # Terminal size sanity check
    local -i term_cols term_rows
    term_cols=$(tput cols 2>/dev/null) || term_cols=80
    term_rows=$(tput lines 2>/dev/null) || term_rows=24

    if (( term_cols < MIN_COLS || term_rows < MIN_ROWS )); then
        log_err "Terminal too small: ${term_cols}x${term_rows} (need ${MIN_COLS}x${MIN_ROWS})"
        exit 1
    fi

    load_state

    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    if ! stty -icanon -echo min 1 time 0 2>/dev/null; then
        log_err "Failed to configure terminal (stty). Cannot run interactively."
        exit 1
    fi

    printf '%s%s%s%s' "${MOUSE_ON}" "${CURSOR_HIDE}" "${CLR_SCREEN}" "${CURSOR_HOME}"

    local key escape_seq

    while true; do
        draw_ui

        IFS= read -rsn1 key || break

        if [[ "$key" == $'\x1b' ]]; then
            # Atomic reader (matches Master Template)
            read_escape_seq escape_seq

            case "$escape_seq" in
                '[Z')           switch_tab -1 ;;
                '[A'|'OA')      navigate -1 ;;
                '[B'|'OB')      navigate 1 ;;
                '[C'|'OC')      adjust_setting 1 ;;
                '[D'|'OD')      adjust_setting -1 ;;
                '['*'<'*[Mm])   handle_mouse "$escape_seq" ;; # Strict SGR matching
                *)              ;;
            esac
        else
            case "${key}" in
                k|K)            navigate -1 ;;
                j|J)            navigate 1 ;;
                l|L)            adjust_setting 1 ;;
                h|H)            adjust_setting -1 ;;
                $'\t')          switch_tab 1 ;;
                $'\n'|''|' '|o|O) handle_enter ;;
                q|Q|$'\x03')    break ;;
                *)              ;;
            esac
        fi
    done
}

main "$@"
