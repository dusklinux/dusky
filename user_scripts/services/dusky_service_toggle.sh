#!/usr/bin/env bash
# ==============================================================================
# TITLE:        Dusky Service Manager TUI
# DESCRIPTION:  Interactive TUI to toggle user and system systemd services.
# TARGET:       Arch Linux / Hyprland / UWSM / Wayland
# ENGINE:       Dusky TUI Engine v3.9.1 (Adapted)
# VERSION:      3.0.0
# ==============================================================================
#
# v3.0.0 CHANGELOG:
#   - FEATURE: Tab-based UI with "User Services" and "System Services" tabs.
#   - FEATURE: Dynamic service discovery — uninstalled services are hidden.
#   - FEATURE: Sudo credential prompting for system service toggles.
#   - FEATURE: Terminal state save/restore around sudo password entry.
#   - FEATURE: Per-tab scroll and selection state preservation.
#   - FEATURE: Mouse support (click, scroll wheel, tab clicks).
#   - FEATURE: Buffered rendering — zero flicker redraws.
#   - FEATURE: Scroll windowing with indicators for large lists.
#   - FEATURE: Feedback messages with auto-clear countdown.
#   - ENGINE: Full Dusky TUI Engine v3.9.1 pattern compliance.
#   - FIX: Hardened base-10 coercion for all integer contexts.
#   - FIX: Robust signal handling and cleanup on all exit paths.
# ==============================================================================

set -euo pipefail
shopt -s extglob

# =============================================================================
# ▼ USER CONFIGURATION (EDIT THIS SECTION) ▼
# =============================================================================

declare -r APP_TITLE="Dusky Service Manager"
declare -r APP_VERSION="v3.0.0"

# Dimensions & Layout
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=76
declare -ri ADJUST_THRESHOLD=38
declare -ri ITEM_PADDING=36

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

declare -ra TABS=("User Services" "System Services")

# ── Service Definitions ──
# SYNTAX: "service_unit_name.service|Friendly Description"
# Services that are not installed (unit file not found) are silently skipped.

declare -ra USER_SERVICE_DEFS=(
    "hyprsunset.service|Night Light (Blue Light Filter)"
    "battery_notify.service|Battery Level Notifications"
    "network_meter.service|Waybar Network Traffic Monitor"
    "dusky.service|Dusky Background Service"
    "dusky_sliders.service|Dusky Sliders Service"
    "update_checker.timer|Automatic Update Checker"
    "hypridle.service|Hyprland Idle Daemon"
)

declare -ra SYSTEM_SERVICE_DEFS=(
    "vsftpd.service|FTP Server (vsftpd)"
    "tlp.service|TLP Power Management"
    "swayosd-libinput-backend.service|SwayOSD Input Backend"
    "sshd.service|SSH Server (OpenSSH)"
    "warp-svc.service|Cloudflare WARP VPN"
    "firewalld.service|Firewall (firewalld)"
    "ufw.service|Firewall (UFW)"
)

# Post-Toggle Hook
post_toggle_action() {
    : # e.g., notify-send, bar reload, etc.
}

# =============================================================================
# ▲ END OF USER CONFIGURATION ▲
# =============================================================================

# --- Pre-computed Constants ---
declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" ''
declare -r H_LINE="${_h_line_buf// /─}"
unset _h_line_buf

# --- ANSI Constants ---
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

# Increased timeout for SSH/remote reliability
declare -r ESC_READ_TIMEOUT=0.10

# --- State Management ---
declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=0
declare -i SCROLL_OFFSET=0
declare -ri TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare ORIGINAL_STTY=""

# Per-tab state preservation
declare -a TAB_SAVED_ROW=()
declare -a TAB_SAVED_SCROLL=()
for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    TAB_SAVED_ROW+=("0")
    TAB_SAVED_SCROLL+=("0")
done
unset _ti

# Feedback message system
declare FEEDBACK_MSG=""
declare -i FEEDBACK_COUNTDOWN=0

# Sudo credential state
declare -i SUDO_AUTHENTICATED=0

# --- Data Structures ---
# Each tab has a parallel set of arrays:
#   TAB_ITEMS_N     - display labels (friendly names)
#   TAB_UNITS_N     - systemd unit names
#   TAB_STATUS_N    - cached status ("active" / "inactive")
#   TAB_SCOPE_N     - "user" or "system"

for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    declare -ga "TAB_ITEMS_${_ti}=()"
    declare -ga "TAB_UNITS_${_ti}=()"
    declare -ga "TAB_STATUS_${_ti}=()"
    declare -ga "TAB_SCOPE_${_ti}=()"
done
unset _ti

# --- System Helpers ---

log_err() {
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
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

# --- String Helpers ---

# Robust ANSI stripping using extglob parameter expansion.
strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
    REPLY="$v"
}

# --- Service Discovery & Registration ---

# Check if a systemd unit file exists (installed) without querying state.
# For user services: checks if the unit is known to the user manager.
# For system services: checks if the unit is known to the system manager.
# Returns 0 if installed, 1 if not.
is_unit_installed() {
    local unit="$1" scope="$2"
    if [[ "$scope" == "user" ]]; then
        # list-unit-files shows all known units; grep for exact match
        systemctl --user list-unit-files "$unit" --no-pager --no-legend 2>/dev/null | grep -q . 2>/dev/null
    else
        systemctl list-unit-files "$unit" --no-pager --no-legend 2>/dev/null | grep -q . 2>/dev/null
    fi
}

# Register services into tab arrays, skipping uninstalled ones.
# This runs once at startup — O(n) systemctl calls, not per-frame.
discover_and_register() {
    local -i tab_idx
    local entry unit_name description scope

    # Tab 0: User Services
    tab_idx=0
    for entry in "${USER_SERVICE_DEFS[@]}"; do
        unit_name="${entry%%|*}"
        description="${entry##*|}"
        if is_unit_installed "$unit_name" "user"; then
            local -n _items_ref="TAB_ITEMS_${tab_idx}"
            local -n _units_ref="TAB_UNITS_${tab_idx}"
            local -n _status_ref="TAB_STATUS_${tab_idx}"
            local -n _scope_ref="TAB_SCOPE_${tab_idx}"
            _items_ref+=("$description")
            _units_ref+=("$unit_name")
            _status_ref+=("unknown")
            _scope_ref+=("user")
        fi
    done

    # Tab 1: System Services
    tab_idx=1
    for entry in "${SYSTEM_SERVICE_DEFS[@]}"; do
        unit_name="${entry%%|*}"
        description="${entry##*|}"
        if is_unit_installed "$unit_name" "system"; then
            local -n _items_ref2="TAB_ITEMS_${tab_idx}"
            local -n _units_ref2="TAB_UNITS_${tab_idx}"
            local -n _status_ref2="TAB_STATUS_${tab_idx}"
            local -n _scope_ref2="TAB_SCOPE_${tab_idx}"
            _items_ref2+=("$description")
            _units_ref2+=("$unit_name")
            _status_ref2+=("unknown")
            _scope_ref2+=("system")
        fi
    done
}

# --- Status Management ---

# Refresh all statuses for the current tab
refresh_tab_statuses() {
    local -i tab=${1:-$CURRENT_TAB}
    local -n _r_units="TAB_UNITS_${tab}"
    local -n _r_status="TAB_STATUS_${tab}"
    local -n _r_scope="TAB_SCOPE_${tab}"
    local -i count=${#_r_units[@]}
    local -i i

    for (( i = 0; i < count; i++ )); do
        if [[ "${_r_scope[i]}" == "user" ]]; then
            if systemctl --user is-active --quiet "${_r_units[i]}" 2>/dev/null; then
                _r_status[i]="active"
            else
                _r_status[i]="inactive"
            fi
        else
            if systemctl is-active --quiet "${_r_units[i]}" 2>/dev/null; then
                _r_status[i]="active"
            else
                _r_status[i]="inactive"
            fi
        fi
    done
}

# Refresh a single service status after toggle
refresh_single_status() {
    local -i idx=$1
    local -n _rs_units="TAB_UNITS_${CURRENT_TAB}"
    local -n _rs_status="TAB_STATUS_${CURRENT_TAB}"
    local -n _rs_scope="TAB_SCOPE_${CURRENT_TAB}"

    if [[ "${_rs_scope[idx]}" == "user" ]]; then
        if systemctl --user is-active --quiet "${_rs_units[idx]}" 2>/dev/null; then
            _rs_status[idx]="active"
        else
            _rs_status[idx]="inactive"
        fi
    else
        if systemctl is-active --quiet "${_rs_units[idx]}" 2>/dev/null; then
            _rs_status[idx]="active"
        else
            _rs_status[idx]="inactive"
        fi
    fi
}

# --- Sudo Credential Management ---

# Temporarily restore terminal to cooked mode, show cursor, disable mouse,
# prompt for sudo password, then re-enter raw TUI mode.
# Returns 0 if sudo credentials are now valid, 1 on failure/cancel.
acquire_sudo() {
    # Already have valid credentials?
    if sudo -n true 2>/dev/null; then
        SUDO_AUTHENTICATED=1
        return 0
    fi

    # ── Temporarily exit TUI mode ──
    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "$ORIGINAL_STTY" 2>/dev/null || :
    fi

    # Clear screen and show prompt
    printf '%s%s' "$CLR_SCREEN" "$CURSOR_HOME"
    printf '\n'
    printf '  %s┌────────────────────────────────────────────────┐%s\n' "$C_MAGENTA" "$C_RESET"
    printf '  %s│%s  System services require administrator access  %s│%s\n' "$C_MAGENTA" "$C_YELLOW" "$C_MAGENTA" "$C_RESET"
    printf '  %s└────────────────────────────────────────────────┘%s\n' "$C_MAGENTA" "$C_RESET"
    printf '\n'

    local -i result=0
    # sudo -v validates/refreshes credentials with user interaction
    sudo -v 2>/dev/null || result=$?

    # ── Re-enter TUI mode ──
    stty -icanon -echo min 1 time 0 2>/dev/null
    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"

    if (( result == 0 )); then
        SUDO_AUTHENTICATED=1
        FEEDBACK_MSG="${C_GREEN}■ Authentication successful${C_RESET}"
        FEEDBACK_COUNTDOWN=3
        return 0
    else
        FEEDBACK_MSG="${C_RED}■ Authentication failed or cancelled${C_RESET}"
        FEEDBACK_COUNTDOWN=4
        return 1
    fi
}

# --- Toggle Logic ---

toggle_selected() {
    local -n _t_items="TAB_ITEMS_${CURRENT_TAB}"
    local -n _t_units="TAB_UNITS_${CURRENT_TAB}"
    local -n _t_status="TAB_STATUS_${CURRENT_TAB}"
    local -n _t_scope="TAB_SCOPE_${CURRENT_TAB}"
    local -i count=${#_t_items[@]}

    if (( count == 0 )); then return 0; fi

    local unit_name="${_t_units[SELECTED_ROW]}"
    local display_name="${_t_items[SELECTED_ROW]}"
    local scope="${_t_scope[SELECTED_ROW]}"
    local current_state="${_t_status[SELECTED_ROW]}"
    local -i result=0

    # ── Sudo gate for system services ──
    if [[ "$scope" == "system" ]]; then
        if ! sudo -n true 2>/dev/null; then
            if ! acquire_sudo; then
                return 0
            fi
        fi
    fi

    # ── Perform toggle ──
    if [[ "$current_state" == "active" ]]; then
        if [[ "$scope" == "user" ]]; then
            systemctl --user disable --now "$unit_name" &>/dev/null || result=$?
        else
            sudo systemctl disable --now "$unit_name" &>/dev/null || result=$?
        fi
        if (( result == 0 )); then
            FEEDBACK_MSG="${C_YELLOW}■ Stopped:${C_RESET} ${display_name}"
        else
            FEEDBACK_MSG="${C_RED}■ Failed to stop:${C_RESET} ${display_name}"
        fi
    else
        if [[ "$scope" == "user" ]]; then
            systemctl --user enable --now "$unit_name" &>/dev/null || result=$?
        else
            sudo systemctl enable --now "$unit_name" &>/dev/null || result=$?
        fi
        if (( result == 0 )); then
            FEEDBACK_MSG="${C_GREEN}■ Started:${C_RESET} ${display_name}"
        else
            FEEDBACK_MSG="${C_RED}■ Failed to start:${C_RESET} ${display_name}"
        fi
    fi

    FEEDBACK_COUNTDOWN=3
    refresh_single_status "$SELECTED_ROW"

    if (( result == 0 )); then
        post_toggle_action
    fi
}

# --- UI Rendering Engine ---

# Computes scroll window and clamps SELECTED_ROW
# Sets: SCROLL_OFFSET, SELECTED_ROW, _vis_start, _vis_end
# Note: _vis_start/_vis_end are resolved via Bash dynamic scoping
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

# Renders scroll indicators (above/below items)
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

# Render service item list for the current tab
render_service_list() {
    local -n _rsl_buf=$1
    local -i _rsl_vs=$2 _rsl_ve=$3

    local -n _rsl_items="TAB_ITEMS_${CURRENT_TAB}"
    local -n _rsl_units="TAB_UNITS_${CURRENT_TAB}"
    local -n _rsl_status="TAB_STATUS_${CURRENT_TAB}"
    local -n _rsl_scope="TAB_SCOPE_${CURRENT_TAB}"

    local -i ri
    local item unit_name status scope status_display padded_item scope_badge

    for (( ri = _rsl_vs; ri < _rsl_ve; ri++ )); do
        item="${_rsl_items[ri]}"
        unit_name="${_rsl_units[ri]}"
        status="${_rsl_status[ri]}"
        scope="${_rsl_scope[ri]}"

        # Status indicator
        if [[ "$status" == "active" ]]; then
            status_display="${C_GREEN}● ON ${C_RESET}"
        else
            status_display="${C_RED}○ OFF${C_RESET}"
        fi

        # Scope badge for visual clarity
        if [[ "$scope" == "system" ]]; then
            scope_badge="${C_YELLOW}⚙${C_RESET} "
        else
            scope_badge="  "
        fi

        printf -v padded_item "%-${ITEM_PADDING}s" "${item:0:${ITEM_PADDING}}"

        if (( ri == SELECTED_ROW )); then
            _rsl_buf+="${C_CYAN} ➤ ${C_INVERSE}${status_display} ${scope_badge}${padded_item}${C_RESET}${CLR_EOL}"$'\n'
        else
            _rsl_buf+="   ${status_display} ${scope_badge}${padded_item}${CLR_EOL}"$'\n'
        fi
    done

    # Fill empty rows to prevent visual artifacts
    local -i rows_rendered=$(( _rsl_ve - _rsl_vs ))
    for (( ri = rows_rendered; ri < MAX_DISPLAY_ROWS; ri++ )); do
        _rsl_buf+="${CLR_EOL}"$'\n'
    done
}

draw_ui() {
    local buf="" pad_buf=""
    local -i i current_col=3 zone_start len count pad_needed
    local -i left_pad right_pad vis_len
    local -i _vis_start _vis_end

    buf+="${CURSOR_HOME}"

    # ── Top border ──
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'

    # ── Title line ──
    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local -i v_len=${#REPLY}
    vis_len=$(( t_len + v_len + 1 ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    # ── Tab line ──
    local tab_line="${C_MAGENTA}│ "
    TAB_ZONES=()

    for (( i = 0; i < TAB_COUNT; i++ )); do
        local name="${TABS[i]}"
        len=${#name}
        zone_start=$current_col

        # Show count of services in each tab
        local -n _tc_items="TAB_ITEMS_${i}"
        local tab_label="${name} (${#_tc_items[@]})"
        local tab_label_len=${#tab_label}

        if (( i == CURRENT_TAB )); then
            tab_line+="${C_CYAN}${C_INVERSE} ${tab_label} ${C_RESET}${C_MAGENTA}│ "
        else
            tab_line+="${C_GREY} ${tab_label} ${C_MAGENTA}│ "
        fi
        TAB_ZONES+=("${zone_start}:$(( zone_start + tab_label_len + 1 ))")
        current_col=$(( current_col + tab_label_len + 4 ))
    done

    pad_needed=$(( BOX_INNER_WIDTH - current_col + 2 ))
    if (( pad_needed < 0 )); then pad_needed=0; fi

    if (( pad_needed > 0 )); then
        printf -v pad_buf '%*s' "$pad_needed" ''
        tab_line+="${pad_buf}"
    fi
    tab_line+="${C_MAGENTA}│${C_RESET}"

    buf+="${tab_line}${CLR_EOL}"$'\n'

    # ── Bottom border ──
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    # ── Service list ──
    local -n _draw_items="TAB_ITEMS_${CURRENT_TAB}"
    count=${#_draw_items[@]}

    compute_scroll_window "$count"
    render_scroll_indicator buf "above" "$count" "$_vis_start"

    if (( count == 0 )); then
        buf+="${CLR_EOL}"$'\n'
        buf+="    ${C_GREY}No services found in this category.${C_RESET}${CLR_EOL}"$'\n'
        buf+="${CLR_EOL}"$'\n'
        local -i ri
        for (( ri = 3; ri < MAX_DISPLAY_ROWS; ri++ )); do
            buf+="${CLR_EOL}"$'\n'
        done
    else
        render_service_list buf "$_vis_start" "$_vis_end"
    fi

    render_scroll_indicator buf "below" "$count" "$_vis_end"

    # ── Detail line: show unit name for selected service ──
    if (( count > 0 )); then
        local -n _det_units="TAB_UNITS_${CURRENT_TAB}"
        local -n _det_scope="TAB_SCOPE_${CURRENT_TAB}"
        local scope_label
        if [[ "${_det_scope[SELECTED_ROW]}" == "system" ]]; then
            scope_label="${C_YELLOW}system${C_RESET}"
        else
            scope_label="${C_CYAN}user${C_RESET}"
        fi
        buf+=$'\n'" ${C_GREY}Unit:${C_RESET} ${C_WHITE}${_det_units[SELECTED_ROW]}${C_RESET}  ${C_GREY}Scope:${C_RESET} ${scope_label}${CLR_EOL}"$'\n'
    else
        buf+=$'\n'"${CLR_EOL}"$'\n'
    fi

    # ── Feedback line ──
    if (( FEEDBACK_COUNTDOWN > 0 )); then
        buf+=" ${FEEDBACK_MSG}${CLR_EOL}"$'\n'
        (( FEEDBACK_COUNTDOWN-- ))
    else
        buf+="${CLR_EOL}"$'\n'
    fi

    # ── Help line ──
    buf+="${C_CYAN} [Tab] Switch  [↑/↓ j/k] Navigate  [Enter/Space] Toggle  [r] Refresh  [q] Quit${C_RESET}${CLR_EOL}"$'\n'

    # ── Legend line ──
    buf+="${C_GREY}   ${C_GREEN}● ON${C_GREY} = active+enabled   ${C_RED}○ OFF${C_GREY} = inactive   ${C_YELLOW}⚙${C_GREY} = requires sudo${C_RESET}${CLR_EOL}${CLR_EOS}"

    printf '%s' "$buf"
}

# --- Input Handling ---

navigate() {
    local -i dir=$1
    local -n _nav_items="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_nav_items[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
}

navigate_page() {
    local -i dir=$1
    local -n _navp_items="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_navp_items[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
}

navigate_end() {
    local -i target=$1
    local -n _nave_items="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_nave_items[@]}
    if (( count == 0 )); then return 0; fi
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( count - 1 )); fi
}

switch_tab() {
    local -i dir=${1:-1}
    # Save current tab state
    TAB_SAVED_ROW[CURRENT_TAB]=$SELECTED_ROW
    TAB_SAVED_SCROLL[CURRENT_TAB]=$SCROLL_OFFSET

    CURRENT_TAB=$(( (CURRENT_TAB + dir + TAB_COUNT) % TAB_COUNT ))

    # Restore destination tab state
    SELECTED_ROW=${TAB_SAVED_ROW[CURRENT_TAB]}
    SCROLL_OFFSET=${TAB_SAVED_SCROLL[CURRENT_TAB]}

    # Refresh statuses when switching tabs
    refresh_tab_statuses
}

set_tab() {
    local -i idx=$1
    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        # Save current tab state
        TAB_SAVED_ROW[CURRENT_TAB]=$SELECTED_ROW
        TAB_SAVED_SCROLL[CURRENT_TAB]=$SCROLL_OFFSET

        CURRENT_TAB=$idx

        # Restore destination tab state
        SELECTED_ROW=${TAB_SAVED_ROW[CURRENT_TAB]}
        SCROLL_OFFSET=${TAB_SAVED_SCROLL[CURRENT_TAB]}

        refresh_tab_statuses
    fi
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
            if [[ "$char" =~ [a-zA-Z~] ]]; then break; fi
        done
    fi
    return 0
}

handle_mouse() {
    local input="$1"
    local -i button x y i start end

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

    # Scroll wheel
    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi

    # Only process press (M), not release (m)
    if [[ "$terminator" != "M" ]]; then return 0; fi

    # Tab row clicks
    if (( y == TAB_ROW )); then
        local zone
        for (( i = 0; i < TAB_COUNT; i++ )); do
            zone="${TAB_ZONES[i]}"
            start="${zone%%:*}"
            end="${zone##*:}"
            if (( x >= start && x <= end )); then set_tab "$i"; return 0; fi
        done
        return 0
    fi

    # Item area clicks
    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))
        local -n _mouse_items="TAB_ITEMS_${CURRENT_TAB}"
        local -i count=${#_mouse_items[@]}

        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            # Left click on right side toggles
            if (( button == 0 && x > ADJUST_THRESHOLD )); then
                toggle_selected
            fi
        fi
    fi
    return 0
}

handle_key() {
    local key="$1"

    # Escape sequences
    case "$key" in
        '[Z')                switch_tab -1; return ;;
        '[A'|'OA')           navigate -1; return ;;
        '[B'|'OB')           navigate 1; return ;;
        '[C'|'OC')           toggle_selected; return ;;
        '[D'|'OD')           return ;;
        '[5~')               navigate_page -1; return ;;
        '[6~')               navigate_page 1; return ;;
        '[H'|'[1~')          navigate_end 0; return ;;
        '[F'|'[4~')          navigate_end 1; return ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return ;;
    esac

    # Character keys
    case "$key" in
        k|K)            navigate -1 ;;
        j|J)            navigate 1 ;;
        g)              navigate_end 0 ;;
        G)              navigate_end 1 ;;
        $'\t')          switch_tab 1 ;;
        r|R)            refresh_tab_statuses; FEEDBACK_MSG="${C_CYAN}■ Status refreshed${C_RESET}"; FEEDBACK_COUNTDOWN=2 ;;
        ' '|''|$'\n')   toggle_selected ;;
        l|L)            toggle_selected ;;
        h|H)            return ;;
        q|Q|$'\x03')    exit 0 ;;
    esac
}

handle_input_router() {
    local key="$1"
    local escape_seq=""

    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
            if [[ "$key" == "" || "$key" == $'\n' ]]; then
                key=$'\e\n'
            fi
        else
            # Bare ESC — no action in single-view mode
            return
        fi
    fi

    handle_key "$key"
}

# --- Main Entry Point ---

main() {
    # ── Pre-flight checks ──
    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5.0+ required"; exit 1; fi
    if [[ ! -t 0 ]]; then log_err "TTY required"; exit 1; fi

    if (( EUID == 0 )); then
        log_err "This script manages USER services and must not be run as root."
        printf '%s\n' "Please run as a standard user (without sudo)." >&2
        exit 1
    fi

    if ! command -v systemctl &>/dev/null; then
        log_err "Missing dependency: systemctl"
        exit 1
    fi

    # ── Discover installed services ──
    discover_and_register

    # Verify at least one tab has services
    local -i _total=0
    local -i _ti
    for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
        local -n _check_items="TAB_ITEMS_${_ti}"
        _total=$(( _total + ${#_check_items[@]} ))
    done
    if (( _total == 0 )); then
        log_err "No installed services found from configured definitions."
        exit 1
    fi

    # ── Terminal setup ──
    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    stty -icanon -echo min 1 time 0 2>/dev/null

    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"

    # ── Initial status load ──
    refresh_tab_statuses

    # ── Main loop ──
    local key
    while true; do
        draw_ui
        IFS= read -rsn1 key || break
        handle_input_router "$key"
    done
}

main "$@"
