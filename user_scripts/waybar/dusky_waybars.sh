#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky Waybar Manager - Unified Edition v4.8.1 (TUI Engine v3.9.2 Core)
# -----------------------------------------------------------------------------
# Target: Arch Linux / Hyprland / UWSM / Wayland
# Description: High-performance TUI for Waybar theme management.
#
# v4.8.1 CHANGELOG:
#   - CRITICAL FIX: (( )) && ... pattern at end of functions caused set -e to
#     kill the script when arithmetic evaluated false (exit code 1). Every
#     function now ends with explicit return 0 or uses if/then/fi guards.
#   - CRITICAL FIX: Waybar launched via setsid for all final paths; survives
#     kitty/terminal closure from desktop entries and rofi.
#   - CRITICAL FIX: Enter key detected via read return code tracking. read
#     -rsn1 returns empty string for Enter; old code silently discarded it.
#   - CRITICAL FIX: --toggle/--back_toggle no longer double kill+restart.
#     CLEANUP_SKIP_WAYBAR prevents cleanup from redundantly managing waybar.
#   - FIX: Cancel path does exactly one kill → restore → setsid launch.
#   - RETAINED: Live preview on navigation with debouncing.
#   - RETAINED: All original flags (--toggle, --back_toggle, -h/--help).
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s extglob

export LC_NUMERIC=C

# =============================================================================
# ▼ CONFIGURATION ▼
# =============================================================================

readonly CONFIG_ROOT="${HOME}/.config/waybar"
readonly APP_TITLE="Dusky Waybar Manager"
readonly APP_VERSION="v4.8.1"

readonly -a UWSM_CMD=(uwsm-app -- waybar)

declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_WIDTH=76
declare -ri ITEM_COL_WIDTH=48
declare -ri DEBOUNCE_MS=150

declare -r ESC_READ_TIMEOUT=0.10

# =============================================================================
# ▲ END OF CONFIGURATION ▲
# =============================================================================

declare _hbuf
printf -v _hbuf '%*s' "$BOX_WIDTH" ''
readonly H_LINE="${_hbuf// /─}"
unset _hbuf

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
readonly CLR_EOS=$'\033[J'
readonly CLR_SCREEN=$'\033[2J'
readonly CURSOR_HOME=$'\033[H'
readonly CURSOR_HIDE=$'\033[?25l'
readonly CURSOR_SHOW=$'\033[?25h'
readonly MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h'
readonly MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l'

# --- State ---
declare -i SELECTED_ROW=0
declare -i SCROLL_OFFSET=0
declare ORIGINAL_STTY=""

declare -a THEME_DIRS=()
declare -a THEME_NAMES=()
declare -a THEME_POSITIONS=()

declare -i FINALIZED=0
declare -i USER_QUIT=0
declare -i CLEANUP_SKIP_WAYBAR=0
declare ORIG_CONFIG=""
declare ORIG_STYLE=""
declare _TMPFILE=""

declare LAST_INPUT_TIME="0"
declare -i PREVIEW_DIRTY=0
declare -i PENDING_IDX=-1

# =============================================================================
# SYSTEM HELPERS
# =============================================================================

log_err() { printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; }
log_info() { printf '%s[INFO]%s %s\n' "$C_CYAN" "$C_RESET" "$1"; }
log_ok() { printf '%s[OK]%s %s\n' "$C_GREEN" "$C_RESET" "$1"; }

strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
    REPLY="$v"
}

get_time_ms() {
    local -n _out_ms=$1
    local raw="${EPOCHREALTIME}"
    local seconds="${raw%%.*}"
    local fractional="${raw#*.}"
    fractional="${fractional:0:3}"
    _out_ms="${seconds}${fractional}"
}

kill_waybar() {
    pkill -x waybar 2>/dev/null || :
    local -i i
    for (( i = 0; i < 15; i++ )); do
        pgrep -x waybar &>/dev/null || return 0
        sleep 0.1
    done
    pkill -9 -x waybar 2>/dev/null || :
    sleep 0.2
    return 0
}

force_clean_locks() {
    rm -f "/run/user/${UID}/uwsm-app.lock" 2>/dev/null || :
    return 0
}

# Launch waybar fully detached. setsid = new session, survives terminal closure.
launch_waybar_detached() {
    force_clean_locks
    setsid "${UWSM_CMD[@]}" &>/dev/null &
    disown 2>/dev/null || :
    sleep 0.4
    return 0
}

# Launch waybar for live preview (child of this shell, pkill can find it).
launch_waybar_preview() {
    force_clean_locks
    "${UWSM_CMD[@]}" &>/dev/null &
    disown 2>/dev/null || :
    return 0
}

# =============================================================================
# CLEANUP / TRAP
# =============================================================================

cleanup() {
    local rc=$?

    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "$ORIGINAL_STTY" 2>/dev/null || :
    fi
    if [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" ]]; then
        rm -f "$_TMPFILE" 2>/dev/null || :
    fi
    printf '\n' 2>/dev/null || :

    # If main code already handled waybar lifecycle, just exit.
    if (( CLEANUP_SKIP_WAYBAR )); then
        exit "$rc"
    fi

    # Unexpected exit (signal, error): kill preview, restore original, restart.
    kill_waybar

    if [[ -n "${ORIG_CONFIG:-}" ]]; then
        rm -f "${CONFIG_ROOT}/config.jsonc" "${CONFIG_ROOT}/style.css"
        ln -snf "$ORIG_CONFIG" "${CONFIG_ROOT}/config.jsonc"
        if [[ -n "${ORIG_STYLE:-}" ]]; then
            ln -snf "$ORIG_STYLE" "${CONFIG_ROOT}/style.css"
        fi
    fi

    launch_waybar_detached
    exit "$rc"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# =============================================================================
# CORE LOGIC
# =============================================================================

scan_themes() {
    local dir
    shopt -s nullglob
    local -a candidates=("${CONFIG_ROOT}"/*/config.jsonc)
    shopt -u nullglob

    THEME_DIRS=()
    THEME_NAMES=()

    for dir in "${candidates[@]}"; do
        dir="${dir%/config.jsonc}"
        THEME_DIRS+=("$dir")
        THEME_NAMES+=("${dir##*/}")
    done

    local -i count="${#THEME_NAMES[@]}"
    if (( count == 0 )); then
        log_err "No valid theme directories found in ${CONFIG_ROOT}."
        exit 1
    fi
    return 0
}

find_current_index() {
    local -n _out=$1
    _out=-1
    local cfg="${CONFIG_ROOT}/config.jsonc"
    [[ -e "$cfg" ]] || return 0

    local real_path
    real_path=$(readlink -f "$cfg" 2>/dev/null) || return 0
    local current_dir="${real_path%/*}"

    local -i i resolved_count="${#THEME_DIRS[@]}"
    local resolved
    for (( i = 0; i < resolved_count; i++ )); do
        resolved=$(readlink -f "${THEME_DIRS[i]}") || continue
        if [[ "$resolved" == "$current_dir" ]]; then
            _out=$i
            return 0
        fi
    done
    return 0
}

get_theme_position() {
    local -n _pos_out=$1
    local idx=$2
    local config_file="${THEME_DIRS[idx]}/config.jsonc"

    if [[ ! -r "$config_file" ]]; then
        _pos_out="UNK"
        return 0
    fi

    local content
    content=$(<"$config_file")
    if [[ $content =~ \"position\"[[:space:]]*:[[:space:]]*\"([a-z]+)\" ]]; then
        _pos_out="${BASH_REMATCH[1]}"
    else
        _pos_out="UNK"
    fi
    return 0
}

refresh_positions() {
    THEME_POSITIONS=()
    local -i i count="${#THEME_NAMES[@]}"
    local pos
    for (( i = 0; i < count; i++ )); do
        get_theme_position pos "$i"
        THEME_POSITIONS+=("$pos")
    done
    return 0
}

toggle_position() {
    local -i idx=$1
    local config_file="${THEME_DIRS[idx]}/config.jsonc"
    [[ -w "$config_file" ]] || return 1

    local current_pos="${THEME_POSITIONS[idx]}"
    local target_pos

    case "$current_pos" in
        top)    target_pos="bottom" ;;
        bottom) target_pos="top" ;;
        left)   target_pos="right" ;;
        right)  target_pos="left" ;;
        *)      target_pos="top" ;;
    esac

    _TMPFILE=$(mktemp "${config_file}.tmp.XXXXXXXXXX")

    if ! sed -E "s/(\"position\"[[:space:]]*:[[:space:]]*)\"[^\"]+\"/\1\"${target_pos}\"/" \
         "$config_file" > "$_TMPFILE"; then
        rm -f "$_TMPFILE" 2>/dev/null || :
        _TMPFILE=""
        return 1
    fi

    cat "$_TMPFILE" > "$config_file"
    rm -f "$_TMPFILE"
    _TMPFILE=""

    THEME_POSITIONS[idx]="$target_pos"
    queue_preview "$idx"
    return 0
}

apply_symlinks() {
    local dir="$1"
    rm -f "${CONFIG_ROOT}/config.jsonc" "${CONFIG_ROOT}/style.css"
    ln -snf "${dir}/config.jsonc" "${CONFIG_ROOT}/config.jsonc"
    if [[ -f "${dir}/style.css" ]]; then
        ln -snf "${dir}/style.css" "${CONFIG_ROOT}/style.css"
    fi
    return 0
}

# =============================================================================
# DEBOUNCED PREVIEW ENGINE
# =============================================================================

queue_preview() {
    local -i idx=$1
    PENDING_IDX=$idx
    PREVIEW_DIRTY=1
    get_time_ms LAST_INPUT_TIME
    return 0
}

commit_preview() {
    local -i idx=$PENDING_IDX
    local -i count="${#THEME_NAMES[@]}"
    if (( idx < 0 || idx >= count )); then
        return 0
    fi

    apply_symlinks "${THEME_DIRS[idx]}"
    kill_waybar
    launch_waybar_preview
    PREVIEW_DIRTY=0
    return 0
}

# =============================================================================
# UI RENDERING
# =============================================================================

# CRITICAL: Every (( )) test must use if/then/fi, NOT (( )) && ...,
# because (( )) returns exit code 1 on false, and if the && pattern
# is the last statement in a function, the function returns 1,
# which triggers set -e and kills the script.

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0
        SCROLL_OFFSET=0
        _vis_start=0
        _vis_end=0
        return 0
    fi

    # Clamp SELECTED_ROW
    if (( SELECTED_ROW < 0 )); then
        SELECTED_ROW=0
    fi
    if (( SELECTED_ROW >= count )); then
        SELECTED_ROW=$(( count - 1 ))
    fi

    # Adjust SCROLL_OFFSET to keep selection visible
    if (( SELECTED_ROW < SCROLL_OFFSET )); then
        SCROLL_OFFSET=$SELECTED_ROW
    elif (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )); then
        SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
    fi

    # Clamp SCROLL_OFFSET
    local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
    if (( max_scroll < 0 )); then
        max_scroll=0
    fi
    if (( SCROLL_OFFSET > max_scroll )); then
        SCROLL_OFFSET=$max_scroll
    fi

    _vis_start=$SCROLL_OFFSET
    _vis_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    if (( _vis_end > count )); then
        _vis_end=$count
    fi
    return 0
}

render_scroll_indicator() {
    local -n _rsi_buf=$1
    local position="$2"
    local -i count=$3 boundary=$4
    local inner_line pad

    if [[ "$position" == "above" ]]; then
        if (( SCROLL_OFFSET > 0 )); then
            printf -v inner_line "    ▲ (more above)%*s" "$(( BOX_WIDTH - 18 ))" ""
            _rsi_buf+="${C_MAGENTA}│${C_GREY}${inner_line}${C_MAGENTA}│${C_RESET}${CLR_EOL}"$'\n'
        else
            printf -v inner_line '%*s' "$BOX_WIDTH" ''
            _rsi_buf+="${C_MAGENTA}│${inner_line}│${C_RESET}${CLR_EOL}"$'\n'
        fi
    else
        if (( count > MAX_DISPLAY_ROWS )); then
            local position_info="[$(( SELECTED_ROW + 1 ))/${count}]"
            local -i p_len=${#position_info}
            if (( boundary < count )); then
                local msg="    ▼ (more below) "
                local -i fill=$(( BOX_WIDTH - ${#msg} - p_len ))
                if (( fill < 0 )); then fill=0; fi
                printf -v pad '%*s' "$fill" ''
                _rsi_buf+="${C_MAGENTA}│${C_GREY}${msg}${pad}${position_info}${C_MAGENTA}│${C_RESET}${CLR_EOL}"$'\n'
            else
                local -i fill=$(( BOX_WIDTH - p_len - 1 ))
                if (( fill < 0 )); then fill=0; fi
                printf -v pad '%*s' "$fill" ''
                _rsi_buf+="${C_MAGENTA}│${C_GREY}${pad}${position_info} ${C_MAGENTA}│${C_RESET}${CLR_EOL}"$'\n'
            fi
        else
            printf -v inner_line '%*s' "$BOX_WIDTH" ''
            _rsi_buf+="${C_MAGENTA}│${inner_line}│${C_RESET}${CLR_EOL}"$'\n'
        fi
    fi
    return 0
}

draw_ui() {
    local buf="" pad="" inner_line=""
    local -i count="${#THEME_NAMES[@]}"
    local -i i vis_len left_pad right_pad
    local item p_val p_str padded_name pos_tag status
    local -i fill rows_rendered
    local -i _vis_start _vis_end

    compute_scroll_window "$count"

    # Header
    buf+="${CURSOR_HOME}"
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'

    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local -i v_len=${#REPLY}

    vis_len=$(( t_len + v_len + 1 ))
    left_pad=$(( (BOX_WIDTH - vis_len) / 2 ))
    right_pad=$(( BOX_WIDTH - vis_len - left_pad ))

    printf -v pad '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad '%*s' "$right_pad" ''
    buf+="${pad}│${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}├${H_LINE}┤${C_RESET}${CLR_EOL}"$'\n'

    # Scroll Indicator (Top)
    render_scroll_indicator buf "above" "$count" "$_vis_start"

    # Render List
    local -ri SEL_FIXED_WIDTH=$(( 3 + 5 + 1 + ITEM_COL_WIDTH + 1 + 8 ))
    local -ri NORM_FIXED_WIDTH=$(( 4 + 5 + 1 + ITEM_COL_WIDTH ))

    for (( i = _vis_start; i < _vis_end; i++ )); do
        item="${THEME_NAMES[i]}"
        if (( ${#item} > ITEM_COL_WIDTH )); then
            item="${item:0:$((ITEM_COL_WIDTH - 1))}…"
        fi

        p_val="${THEME_POSITIONS[i]}"
        case "$p_val" in
            top)    p_str="[TOP]" ;;
            bottom) p_str="[BOT]" ;;
            left)   p_str="[LFT]" ;;
            right)  p_str="[RGT]" ;;
            *)      p_str="[UNK]" ;;
        esac

        printf -v padded_name "%-${ITEM_COL_WIDTH}s" "$item"

        if (( i == SELECTED_ROW )); then
            if [[ "$p_val" == "UNK" ]]; then
                pos_tag="${C_GREY}${p_str}${C_RESET}"
            else
                pos_tag="${C_YELLOW}${p_str}${C_RESET}"
            fi

            if (( PREVIEW_DIRTY )); then
                status="${C_YELLOW}● Wait  ${C_RESET}"
            else
                status="${C_GREEN}● Active${C_RESET}"
            fi

            fill=$(( BOX_WIDTH - SEL_FIXED_WIDTH ))
            if (( fill < 0 )); then fill=0; fi
            printf -v pad '%*s' "$fill" ''

            buf+="${C_MAGENTA}│${C_CYAN} ➤ ${C_INVERSE}${pos_tag} ${padded_name}${C_RESET} ${status}${pad}${C_MAGENTA}│${C_RESET}${CLR_EOL}"$'\n'
        else
            pos_tag="${C_GREY}${p_str}${C_RESET}"

            fill=$(( BOX_WIDTH - NORM_FIXED_WIDTH ))
            if (( fill < 0 )); then fill=0; fi
            printf -v pad '%*s' "$fill" ''

            buf+="${C_MAGENTA}│    ${pos_tag} ${padded_name}${pad}│${C_RESET}${CLR_EOL}"$'\n'
        fi
    done

    # Fill Empty Rows
    rows_rendered=$(( _vis_end - _vis_start ))
    for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do
        printf -v inner_line '%*s' "$BOX_WIDTH" ''
        buf+="${C_MAGENTA}│${inner_line}│${C_RESET}${CLR_EOL}"$'\n'
    done

    # Scroll Indicator (Bottom)
    render_scroll_indicator buf "below" "$count" "$_vis_end"

    # Footer
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_CYAN} [Space] Toggle Position   [↑/↓ j/k] Navigate   [PgUp/PgDn] Page${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_CYAN} [Home/g] First   [End/G] Last   [Enter] Apply   [Esc/q] Cancel${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_CYAN} Config: ${C_WHITE}${CONFIG_ROOT}${C_RESET}${CLR_EOL}${CLR_EOS}"

    printf '%s' "$buf"
    return 0
}

# =============================================================================
# INPUT HANDLING
# =============================================================================

navigate() {
    local -i dir=$1
    local -i count="${#THEME_NAMES[@]}"
    if (( count == 0 )); then
        return 0
    fi
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
    queue_preview "$SELECTED_ROW"
    return 0
}

navigate_page() {
    local -i dir=$1
    local -i count="${#THEME_NAMES[@]}"
    if (( count == 0 )); then
        return 0
    fi
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then
        SELECTED_ROW=0
    fi
    if (( SELECTED_ROW >= count )); then
        SELECTED_ROW=$(( count - 1 ))
    fi
    queue_preview "$SELECTED_ROW"
    return 0
}

navigate_end() {
    local -i target=$1
    local -i count="${#THEME_NAMES[@]}"
    if (( count == 0 )); then
        return 0
    fi
    if (( target == 0 )); then
        SELECTED_ROW=0
    else
        SELECTED_ROW=$(( count - 1 ))
    fi
    queue_preview "$SELECTED_ROW"
    return 0
}

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
            if [[ "$char" =~ [a-zA-Z~] ]]; then
                break
            fi
        done
    fi
    return 0
}

handle_mouse() {
    local input="$1"
    local body="${input#'[<'}"
    if [[ "$body" == "$input" ]]; then
        return 0
    fi

    local terminator="${body: -1}"
    if [[ "$terminator" != "M" && "$terminator" != "m" ]]; then
        return 0
    fi

    body="${body%[Mm]}"
    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"

    if [[ ! "$field1" =~ ^[0-9]+$ ]]; then return 0; fi
    if [[ ! "$field2" =~ ^[0-9]+$ ]]; then return 0; fi
    if [[ ! "$field3" =~ ^[0-9]+$ ]]; then return 0; fi

    local -i button=$field1 y=$field3

    if (( button == 64 )); then
        navigate -1
        return 0
    fi
    if (( button == 65 )); then
        navigate 1
        return 0
    fi

    if [[ "$terminator" != "M" ]]; then
        return 0
    fi

    local -i item_row_start=5
    if (( y >= item_row_start && y < item_row_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - item_row_start + SCROLL_OFFSET ))
        local -i count="${#THEME_NAMES[@]}"
        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            queue_preview "$SELECTED_ROW"
        fi
    fi
    return 0
}

# Enter: finalize selection. Kill preview, relaunch detached, show feedback.
do_finalize() {
    FINALIZED=1
    CLEANUP_SKIP_WAYBAR=1

    # Flush any pending preview (ensures symlinks point to selected theme)
    if (( PREVIEW_DIRTY )); then
        apply_symlinks "${THEME_DIRS[PENDING_IDX]}"
        PREVIEW_DIRTY=0
    fi

    # Visual feedback
    local -i footer_line=$(( 3 + 1 + MAX_DISPLAY_ROWS + 1 + 1 + 1 ))
    printf '\033[%d;1H' "$footer_line"
    printf '%s %s✓ Applied:%s %s%s%s%s' \
        "$CLR_EOL" "$C_GREEN" "$C_RESET" "$C_WHITE" \
        "${THEME_NAMES[SELECTED_ROW]}" "$C_RESET" "$CLR_EOL"
    printf '\n %sRestarting waybar...%s%s' "$C_YELLOW" "$C_RESET" "$CLR_EOL"

    # Kill preview waybar, relaunch detached (survives terminal close)
    kill_waybar
    launch_waybar_detached

    return 0
}

# Esc/q: cancel. Kill preview, restore original, relaunch detached.
do_cancel() {
    USER_QUIT=1
    CLEANUP_SKIP_WAYBAR=1

    kill_waybar

    if [[ -n "${ORIG_CONFIG:-}" ]]; then
        rm -f "${CONFIG_ROOT}/config.jsonc" "${CONFIG_ROOT}/style.css"
        ln -snf "$ORIG_CONFIG" "${CONFIG_ROOT}/config.jsonc"
        if [[ -n "${ORIG_STYLE:-}" ]]; then
            ln -snf "$ORIG_STYLE" "${CONFIG_ROOT}/style.css"
        fi
    fi

    launch_waybar_detached
    return 0
}

handle_key() {
    local key="$1"

    # Handle escape sequences
    case "$key" in
        '[A'|'OA')       navigate -1; return 0 ;;
        '[B'|'OB')       navigate  1; return 0 ;;
        '[5~')           navigate_page -1; return 0 ;;
        '[6~')           navigate_page  1; return 0 ;;
        '[H'|'[1~')      navigate_end 0; return 0 ;;
        '[F'|'[4~')      navigate_end 1; return 0 ;;
        '['*'<'*[Mm])    handle_mouse "$key"; return 0 ;;
    esac

    # Handle regular keys
    case "$key" in
        k|K)            navigate -1 ;;
        j|J)            navigate  1 ;;
        g)              navigate_end 0 ;;
        G)              navigate_end 1 ;;
        ' ')
            if (( ${#THEME_NAMES[@]} > 0 )); then
                toggle_position "$SELECTED_ROW"
            fi
            ;;
        ENTER)
            do_finalize
            return 1  # Signal main loop to break
            ;;
        $'\x7f'|$'\x08'|$'\e\n')
            if (( ${#THEME_NAMES[@]} > 0 )); then
                toggle_position "$SELECTED_ROW"
            fi
            ;;
        q|Q|$'\x03')
            do_cancel
            return 1  # Signal main loop to break
            ;;
        ESC)
            do_cancel
            return 1  # Signal main loop to break
            ;;
        *)  ;;
    esac
    return 0
}

handle_input_router() {
    local key="$1"
    local escape_seq=""

    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
            # Alt+Enter detection (ESC followed by empty/newline)
            if [[ "$key" == "" || "$key" == $'\n' ]]; then
                key=$'\e\n'
            fi
        else
            key="ESC"
        fi
    fi

    handle_key "$key"
    return $?
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    local -i opt_toggle=0 opt_back=0

    while (( $# )); do
        case "$1" in
            --toggle)      opt_toggle=1 ;;
            --back_toggle) opt_back=1 ;;
            -h|--help)
                printf 'Usage: %s [--toggle | --back_toggle]\n' "${0##*/}"
                exit 0
                ;;
            *)  ;;
        esac
        shift
    done

    # Verify Bash 5.0+ for EPOCHREALTIME
    if (( BASH_VERSINFO[0] < 5 )) || [[ -z "${EPOCHREALTIME:-}" ]]; then
        log_err "Bash 5.0+ required (EPOCHREALTIME not available)."
        exit 1
    fi

    # TTY check
    if [[ ! -t 0 && opt_toggle -eq 0 && opt_back -eq 0 ]]; then
        log_err "TTY required for interactive mode."
        exit 1
    fi

    # Dependencies Check
    local dep
    for dep in waybar uwsm-app stty sed setsid; do
        if ! command -v "$dep" &>/dev/null; then
            log_err "Required dependency not found: ${dep}"
            exit 1
        fi
    done
    [[ -d "$CONFIG_ROOT" ]] || { log_err "Directory ${CONFIG_ROOT} missing."; exit 1; }

    scan_themes
    refresh_positions

    local -i total="${#THEME_NAMES[@]}"
    local -i cur_idx
    find_current_index cur_idx

    # ── TOGGLE MODE (No TUI) ──
    if (( opt_toggle || opt_back )); then
        CLEANUP_SKIP_WAYBAR=1
        FINALIZED=1

        local -i target_idx
        local cur_name="(unknown)"
        if (( cur_idx >= 0 )); then
            cur_name="${THEME_NAMES[cur_idx]}"
        fi

        if (( cur_idx < 0 )); then
            target_idx=0
        elif (( opt_toggle )); then
            target_idx=$(( (cur_idx + 1) % total ))
        else
            target_idx=$(( (cur_idx - 1 + total) % total ))
        fi

        log_info "Switching: '${cur_name}' -> '${THEME_NAMES[target_idx]}'"
        apply_symlinks "${THEME_DIRS[target_idx]}"
        kill_waybar
        launch_waybar_detached
        log_ok "Applied: ${THEME_NAMES[target_idx]}"
        exit 0
    fi

    # ── TUI MODE ──
    if [[ -L "${CONFIG_ROOT}/config.jsonc" ]]; then
        ORIG_CONFIG=$(readlink "${CONFIG_ROOT}/config.jsonc")
    fi
    if [[ -L "${CONFIG_ROOT}/style.css" ]]; then
        ORIG_STYLE=$(readlink "${CONFIG_ROOT}/style.css")
    fi

    if (( cur_idx >= 0 )); then
        SELECTED_ROW=$cur_idx
    fi

    force_clean_locks

    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    stty -icanon -echo min 1 time 0 2>/dev/null || :

    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"

    queue_preview "$SELECTED_ROW"
    commit_preview

    local key=""
    local -i got_input=0
    local current_time_ms

    while true; do
        draw_ui

        got_input=0
        key=""

        if (( PREVIEW_DIRTY )); then
            get_time_ms current_time_ms
            if (( current_time_ms - LAST_INPUT_TIME > DEBOUNCE_MS )); then
                commit_preview
                continue
            fi
            # Short poll while waiting for debounce
            if IFS= read -rsn1 -t 0.05 key; then
                got_input=1
            else
                # Timeout, no input — loop to recheck debounce
                continue
            fi
        else
            # Block for input
            if IFS= read -rsn1 key; then
                got_input=1
            fi
        fi

        # Detect Enter: read succeeded but key is empty string
        if (( got_input )) && [[ -z "$key" ]]; then
            key="ENTER"
        fi

        # No input received (shouldn't happen in blocking mode, but guard)
        if (( ! got_input )); then
            continue
        fi

        # Route input
        if ! handle_input_router "$key"; then
            break
        fi
    done

    if (( FINALIZED )); then
        log_ok "Applied: ${THEME_NAMES[SELECTED_ROW]}"
    fi

    return 0
}

main "$@"
