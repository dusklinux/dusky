#!/usr/bin/env bash
#
# HyprMonitorWizard v5.6 — Fixed Logic for Command Substitution
#
set -euo pipefail
shopt -s extglob

# ═══════════════════════════════════════════════════════════════════════════════
# GLOBALS & INITIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

declare -a TEMP_FILES=()

cleanup() {
    local f
    for f in "${TEMP_FILES[@]}"; do
        [[ -f "$f" ]] && rm -f -- "$f"
    done
}
trap cleanup EXIT

readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hypr/edit_here"
readonly BACKUP_DIR="/tmp/hypr-wizard-backups"
CONFIG_FILE="${CONFIG_DIR}/monitors.lua"
[[ -f "${CONFIG_DIR}/source/monitors.lua" ]] && CONFIG_FILE="${CONFIG_DIR}/source/monitors.lua"

# ANSI color codes
readonly C_R=$'\e[0m' C_RED=$'\e[31m' C_GRN=$'\e[32m' C_YLW=$'\e[33m'
readonly C_BLU=$'\e[34m' C_CYN=$'\e[36m' C_BLD=$'\e[1m' C_DIM=$'\e[2m'

# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

# CRITICAL FIX: All UI/Logging must go to stderr (>&2) to avoid polluting
# variable captures like $(get_position)
die()  { printf '%s[✖] %s%s\n' "$C_RED" "$1" "$C_R" >&2; exit 1; }
info() { printf '%s[i] %s%s\n' "$C_BLU" "$1" "$C_R" >&2; }
ok()   { printf '%s[✔] %s%s\n' "$C_GRN" "$1" "$C_R" >&2; }
warn() { printf '%s[!] %s%s\n' "$C_YLW" "$1" "$C_R" >&2; }

drain_input() {
    while IFS= read -r -t 0.05 -n 1 2>/dev/null; do :; done
}

check_deps() {
    [[ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]] && die "Hyprland not running"
    command -v jq &>/dev/null || die "jq required: sudo pacman -S jq"
    command -v awk &>/dev/null || die "awk required" 
    mkdir -p -- "$BACKUP_DIR"
    [[ -f "$CONFIG_FILE" ]] || { mkdir -p -- "$(dirname "$CONFIG_FILE")"; : > "$CONFIG_FILE"; }
}

get_monitors_json() {
    hyprctl monitors all -j 2>/dev/null || printf '[]\n'
}

get_active_json() {
    hyprctl monitors -j 2>/dev/null || printf '[]\n'
}

get_field() {
    local name="$1" field="$2"
    get_active_json | jq -r --arg n "$name" --arg f "$field" \
        '.[] | select(.name==$n) | .[$f] // empty'
}

escape_regex() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\[/\\[}"
    s="${s//\]/\\]}"
    s="${s//./\\.}"
    s="${s//^/\\^}"
    s="${s//\$/\\$}"
    s="${s//\*/\\*}"
    printf '%s' "$s"
}

menu() {
    local prompt="$1"; shift
    local -a opts=("$@")
    local input i
    
    drain_input
    
    # CRITICAL FIX: Print menu to stderr >&2
    printf '\n%s%s%s\n' "$C_BLD" "$prompt" "$C_R" >&2
    for i in "${!opts[@]}"; do
        printf '  %s[%d]%s %s\n' "$C_CYN" "$((i+1))" "$C_R" "${opts[i]}" >&2
    done
    
    while :; do
        IFS= read -rp "> " input || input=""
        input="${input#"${input%%[![:space:]]*}"}"
        input="${input%"${input##*[![:space:]]}"}"
        
        [[ -z "$input" ]] && continue
        
        if [[ "$input" =~ ^[0-9]+$ ]] && (( input >= 1 && input <= ${#opts[@]} )); then
            REPLY="$input"
            return 0
        fi
        # CRITICAL FIX: Print error to stderr >&2
        printf '%sEnter 1-%d%s\n' "$C_RED" "${#opts[@]}" "$C_R" >&2
    done
}

confirm() {
    local msg="${1:-Continue?}" reply
    IFS= read -rp "$msg [y/N]: " reply || reply=""
    [[ "${reply,,}" == y || "${reply,,}" == yes ]]
}

pause() {
    local dummy
    IFS= read -rp "Press Enter to continue..." dummy || true
}

backup_config() {
    if [[ -s "$CONFIG_FILE" ]]; then
        local ts
        printf -v ts '%(%s)T' -1
        cp -- "$CONFIG_FILE" "$BACKUP_DIR/monitors.${ts}.bak"
    fi
}

save_rule() {
    local name="$1" rule="$2"
    backup_config
    
    local tmp escaped_name
    tmp=$(mktemp)
    TEMP_FILES+=("$tmp")
    escaped_name=$(escape_regex "$name")
    
    grep -v "^[[:space:]]*monitor[[:space:]]*=[[:space:]]*${escaped_name}[,[:space:]]" \
        -- "$CONFIG_FILE" > "$tmp" 2>/dev/null || true
    printf '%s\n' "$rule" >> "$tmp"
    mv -- "$tmp" "$CONFIG_FILE"
    ok "Saved: $rule"
}

# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

get_misc_option() {
    local option="$1"
    hyprctl getoption "misc:$option" -j 2>/dev/null | jq -r '.int // 0'
}

set_misc_option() {
    local option="$1" value="$2"
    hyprctl keyword "misc:$option" "$value" >/dev/null 2>&1
}

save_misc_option() {
    local option="$1" value="$2"
    backup_config
    
    local tmp in_misc=0 found=0 line
    tmp=$(mktemp)
    TEMP_FILES+=("$tmp")
    
    if grep -q "^[[:space:]]*misc[[:space:]]*{" "$CONFIG_FILE" 2>/dev/null; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            if [[ "$line" =~ ^[[:space:]]*misc[[:space:]]*\{ ]]; then
                in_misc=1
                printf '%s\n' "$line"
            elif (( in_misc )) && [[ "$line" =~ ^[[:space:]]*\} ]]; then
                if (( !found )); then
                    printf '    %s = %s\n' "$option" "$value"
                fi
                in_misc=0
                printf '%s\n' "$line"
            elif (( in_misc )) && [[ "$line" =~ ^[[:space:]]*#?[[:space:]]*${option}[[:space:]]*= ]]; then
                printf '    %s = %s\n' "$option" "$value"
                found=1
            else
                printf '%s\n' "$line"
            fi
        done < "$CONFIG_FILE" > "$tmp"
    else
        cp -- "$CONFIG_FILE" "$tmp"
        printf '\n# Global Display Settings\nmisc {\n    %s = %s\n}\n' "$option" "$value" >> "$tmp"
    fi
    
    mv -- "$tmp" "$CONFIG_FILE"
    ok "Saved to config: misc { $option = $value }"
}

toggle_vfr() {
    local current new_val new_desc
    current=$(get_misc_option "vfr")
    
    if (( current )); then
        new_val="false"
        new_desc="Disabled (constant refresh rate)"
    else
        new_val="true"
        new_desc="Enabled (saves power)"
    fi
    
    printf '\n' >&2
    info "Setting VFR to: $new_desc"
    set_misc_option "vfr" "$new_val"
    sleep 0.3
    ok "VFR is now: $new_desc"
    
    printf '\n' >&2
    if confirm "Save to config file?"; then
        save_misc_option "vfr" "$new_val"
    else
        warn "Change is temporary (until reload/restart)"
    fi
    
    pause
}

set_global_vrr() {
    local current
    current=$(get_misc_option "vrr")
    
    printf '\n' >&2
    printf '%s%sNote:%s Per-monitor VRR settings override this global setting.\n' "$C_DIM" "$C_BLD" "$C_R" >&2
    printf '%s      Use "Configure Monitor" for per-monitor VRR control.%s\n' "$C_DIM" "$C_R" >&2
    
    menu "Set Global VRR (current: $current):" \
        "Off (0) - Disable adaptive sync globally" \
        "On (1) - Enable for all monitors" \
        "Fullscreen Only (2) - Enable only in fullscreen apps" \
        "Fullscreen Video/Game (3) - Fullscreen + video/game content type" \
        "Cancel"
    
    (( REPLY == 5 )) && return
    
    local new_val=$((REPLY - 1))
    local -a vrr_desc=(
        "Off"
        "On (all windows)"
        "Fullscreen Only"
        "Fullscreen Video/Game"
    )
    
    printf '\n' >&2
    info "Setting global VRR to: ${vrr_desc[$new_val]}"
    set_misc_option "vrr" "$new_val"
    sleep 0.3
    ok "Global VRR is now: ${vrr_desc[$new_val]}"
    
    printf '\n' >&2
    if confirm "Save to config file?"; then
        save_misc_option "vrr" "$new_val"
    else
        warn "Change is temporary (until reload/restart)"
    fi
    
    pause
}

global_settings_menu() {
    while :; do
        clear
        printf '%s%s╔═══════════════════════════════════════╗%s\n' "$C_CYN" "$C_BLD" "$C_R"
        printf '%s%s║       Global Display Settings         ║%s\n' "$C_CYN" "$C_BLD" "$C_R"
        printf '%s%s╚═══════════════════════════════════════╝%s\n\n' "$C_CYN" "$C_BLD" "$C_R"
        
        local current_vfr current_vrr
        current_vfr=$(get_misc_option "vfr")
        current_vrr=$(get_misc_option "vrr")
        
        local vfr_status
        if (( current_vfr )); then
            vfr_status="${C_GRN}Enabled${C_R} ${C_DIM}(saves power, recommended)${C_R}"
        else
            vfr_status="${C_YLW}Disabled${C_R} ${C_DIM}(constant refresh, uses more power)${C_R}"
        fi
        
        local vrr_status
        case $current_vrr in
            0) vrr_status="${C_DIM}Off${C_R}" ;;
            1) vrr_status="${C_GRN}On${C_R} ${C_DIM}(all windows)${C_R}" ;;
            2) vrr_status="${C_CYN}Fullscreen Only${C_R}" ;;
            3) vrr_status="${C_CYN}Fullscreen Video/Game${C_R}" ;;
            *) vrr_status="${C_RED}Unknown${C_R} ($current_vrr)" ;;
        esac
        
        printf '%sCurrent Settings:%s\n\n' "$C_BLD" "$C_R"
        printf '  %sVFR%s (Variable Frame Rate):\n' "$C_BLD" "$C_R"
        printf '      Status: %s\n' "$vfr_status"
        printf '      %sReduces GPU work when nothing changes on screen%s\n\n' "$C_DIM" "$C_R"
        
        printf '  %sVRR%s (Variable Refresh Rate / Adaptive Sync):\n' "$C_BLD" "$C_R"
        printf '      Global: %s\n' "$vrr_status"
        printf '      %sSyncs monitor refresh to GPU output (FreeSync/G-Sync)%s\n' "$C_DIM" "$C_R"
        printf '      %sPer-monitor VRR overrides this setting%s\n' "$C_DIM" "$C_R"
        
        menu "Options:" \
            "Toggle VFR (Variable Frame Rate)" \
            "Set Global VRR (Adaptive Sync)" \
            "Back to Main Menu"
        
        case $REPLY in
            1) toggle_vfr ;;
            2) set_global_vrr ;;
            3) return ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════════════════════
# MONITOR CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

apply_config() {
    local cmd="$1" name="$2"
    local before_hz before_scale after_hz after_scale
    
    before_hz=$(get_field "$name" "refreshRate")
    before_scale=$(get_field "$name" "scale")
    
    clear
    printf '%s%s=== Applying Monitor Configuration ===%s\n\n' "$C_YLW" "$C_BLD" "$C_R"
    info "Monitor: $name"
    info "Command: monitor=$cmd"
    info "Before: ${before_hz:-?}Hz, scale ${before_scale:-?}"
    printf '\n' >&2
    
    local escaped_name existing
    escaped_name=$(escape_regex "$name")
    existing=$(grep -n "^[^#]*monitor[[:space:]]*=[[:space:]]*${escaped_name}[,[:space:]]" \
        -- "$CONFIG_FILE" 2>/dev/null | head -1) || true
    [[ -n "$existing" ]] && printf '%sReplacing: %s%s\n\n' "$C_DIM" "$existing" "$C_R" >&2
    
    info "Applying now... (screen may flash)"
    
    hyprctl keyword monitor "$cmd" >/dev/null 2>&1 || true
    sleep 1
    
    clear
    drain_input
    
    printf '%s%s=== Configuration Applied ===%s\n\n' "$C_GRN" "$C_BLD" "$C_R" >&2
    
    after_hz=$(get_field "$name" "refreshRate")
    after_scale=$(get_field "$name" "scale")
    
    info "Monitor: $name"
    ok "Now: ${after_hz:-?}Hz, scale ${after_scale:-?}"
    
    if [[ "$before_hz" != "$after_hz" || "$before_scale" != "$after_scale" ]]; then
        ok "Settings changed successfully!"
    else
        warn "Settings appear unchanged"
    fi
    
    printf '\n' >&2
    if confirm "Keep this configuration?"; then
        save_rule "$name" "monitor=$cmd"
        printf '\n' >&2
        pause
        return 0
    else
        printf '\n' >&2
        warn "Reverting..."
        hyprctl reload >/dev/null 2>&1 || true
        sleep 1
        clear
        drain_input
        info "Reverted to previous config"
        pause
        return 1
    fi
}

get_position() {
    local name="$1"
    local json names_raw
    
    json=$(get_active_json)
    mapfile -t names_raw < <(printf '%s' "$json" | jq -r '.[].name')
    
    local -a names=()
    local n
    for n in "${names_raw[@]}"; do
        [[ "$n" != "$name" ]] && names+=("$n")
    done
    
    if (( ${#names[@]} == 0 )); then
        printf '0x0'
        return
    fi
    
    local anchor="${names[0]}"
    
    if (( ${#names[@]} > 1 )); then
        menu "Position relative to which monitor?" "${names[@]}"
        anchor="${names[$((REPLY-1))]}"
    fi
    
    local ax ay aw ah ascale
    IFS=' ' read -r ax ay aw ah ascale < <(
        printf '%s' "$json" | jq -r --arg n "$anchor" \
            '.[] | select(.name==$n) | "\(.x // 0) \(.y // 0) \(.width // 1920) \(.height // 1080) \(.scale // 1)"'
    )
    
    local law lah
    IFS=' ' read -r law lah < <(awk -v w="$aw" -v h="$ah" -v s="$ascale" 'BEGIN { printf "%.0f %.0f", w/s, h/s }')
    
    menu "Position relative to $anchor:" \
        "Right of $anchor" \
        "Left of $anchor" \
        "Above $anchor" \
        "Below $anchor" \
        "Same position (mirror)" \
        "Custom coordinates"
    
    ax=${ax%%.*} ay=${ay%%.*} law=${law%%.*} lah=${lah%%.*}
    
    case $REPLY in
        1) printf '%dx%d' "$((ax + law))" "$ay" ;;
        2) printf '%dx%d' "$((ax - law))" "$ay" ;;
        3) printf '%dx%d' "$ax" "$((ay - lah))" ;;
        4) printf '%dx%d' "$ax" "$((ay + lah))" ;;
        5) printf '%dx%d' "$ax" "$ay" ;;
        6) 
            local pos
            IFS= read -rp "Position (e.g. 1920x0): " pos || pos="0x0"
            printf '%s' "${pos:-0x0}"
            ;;
    esac
}

configure_monitor() {
    local json
    json=$(get_monitors_json)
    
    local -a names
    mapfile -t names < <(printf '%s' "$json" | jq -r '.[].name')
    
    if (( ${#names[@]} == 0 )); then
        warn "No monitors found"
        pause
        return
    fi
    
    local -a opts=()
    local n
    for n in "${names[@]}"; do
        opts+=("$(printf '%s' "$json" | jq -r --arg n "$n" \
            '.[] | select(.name==$n) | "\(.name): \(.width)x\(.height)@\(.refreshRate)Hz"')")
    done
    opts+=("Cancel")
    
    menu "Select Monitor:" "${opts[@]}"
    (( REPLY == ${#opts[@]} )) && return
    
    local name="${names[$((REPLY-1))]}"
    local mon modes_raw
    mon=$(printf '%s' "$json" | jq --arg n "$name" '.[] | select(.name==$n)')
    
    mapfile -t modes_raw < <(printf '%s' "$mon" | jq -r '.availableModes[]?' 2>/dev/null | sort -t@ -k2 -rn -u)
    
    printf '\n%sAvailable modes for %s:%s\n' "$C_CYN" "$name" "$C_R"
    if (( ${#modes_raw[@]} > 0 )); then
        local idx=1
        for m in "${modes_raw[@]}"; do
            printf '%2d) %s\n' "$idx" "$m"
            ((idx++))
        done
        printf '\n'
    else
        printf '  (none reported)\n'
    fi
    
    local max_mode low_mode
    if (( ${#modes_raw[@]} > 0 )); then
        max_mode="${modes_raw[0]}"
        low_mode=""
        for m in "${modes_raw[@]}"; do
            if [[ "$m" =~ @(59|60|61)\. ]]; then
                low_mode="$m"
                break
            fi
        done
        [[ -z "$low_mode" ]] && low_mode="${modes_raw[-1]}"
    else
        max_mode="preferred"
        low_mode="preferred"
    fi
    
    menu "Resolution & Refresh:" \
        "Preferred (auto)" \
        "Max refresh ($max_mode)" \
        "60Hz ($low_mode)" \
        "Custom" \
        "Disable monitor"
    
    local res
    case $REPLY in
        1) res="preferred" ;;
        2) res="$max_mode" ;;
        3) res="$low_mode" ;;
        4) 
            IFS= read -rp "Mode (e.g. 1920x1080@144.00Hz): " res || res=""
            [[ -z "$res" ]] && res="preferred"
            ;;
        5)
            if confirm "Disable $name?"; then
                apply_config "$name,disable" "$name"
            fi
            return
            ;;
    esac
    
    local cur_scale
    cur_scale=$(printf '%s' "$mon" | jq -r '.scale // 1')
    
    menu "Scale Factor:" \
        "1 (none)" \
        "1.25" \
        "1.33" \
        "1.5" \
        "2" \
        "Keep current ($cur_scale)" \
        "Custom"
    
    local scale
    case $REPLY in
        1) scale="1" ;;
        2) scale="1.25" ;;
        3) scale="1.33" ;;
        4) scale="1.5" ;;
        5) scale="2" ;;
        6) scale="$cur_scale" ;;
        7) 
            IFS= read -rp "Scale: " scale || scale=""
            [[ -z "$scale" ]] && scale="1"
            ;;
    esac
    
    local position
    position=$(get_position "$name")
    
    menu "Rotation:" \
        "Normal (0°)" \
        "90° clockwise" \
        "180°" \
        "270° clockwise"
    local transform=$((REPLY - 1))
    
    local cmd="$name,$res,$position,$scale"
    (( transform > 0 )) && cmd+=",transform,$transform"
    
    printf '\n' >&2
    if confirm "Enable per-monitor VRR for $name?"; then
        cmd+=",vrr,1"
    fi
    
    apply_config "$cmd" "$name"
}

quick_toggle() {
    local json name current_hz current_x current_y current_scale
    
    json=$(get_active_json)
    
    IFS=' ' read -r name current_hz current_x current_y current_scale < <(
        printf '%s' "$json" | jq -r '.[0] | "\(.name) \(.refreshRate) \(.x) \(.y) \(.scale)"'
    )
    
    local modes_raw
    mapfile -t modes_raw < <(get_monitors_json | jq -r --arg n "$name" \
        '.[] | select(.name==$n) | .availableModes[]?' 2>/dev/null | sort -t@ -k2 -rn)
    
    if (( ${#modes_raw[@]} == 0 )); then
        warn "No modes available"
        pause
        return
    fi
    
    local max_mode low_mode
    max_mode="${modes_raw[0]}"
    low_mode=""
    for m in "${modes_raw[@]}"; do
        if [[ "$m" =~ @(59|60|61)\. ]]; then
            low_mode="$m"
            break
        fi
    done
    [[ -z "$low_mode" ]] && low_mode="${modes_raw[-1]}"
    
    printf '\n'
    info "Current: $name @ ${current_hz}Hz"
    
    local target_mode
    local hz_int="${current_hz%%.*}"
    
    if (( hz_int > 65 )); then
        target_mode="$low_mode"
        info "Switching to: Power Save (60Hz)"
    else
        target_mode="$max_mode"
        info "Switching to: Max Refresh ($max_mode)"
    fi
    
    local position="${current_x}x${current_y}"
    apply_config "$name,$target_mode,$position,$current_scale" "$name"
}

mirror_display() {
    local json
    json=$(get_monitors_json)
    
    local -a names
    mapfile -t names < <(printf '%s' "$json" | jq -r '.[].name')
    
    if (( ${#names[@]} < 2 )); then
        warn "Need 2+ monitors for mirroring"
        pause
        return
    fi
    
    menu "Mirror SOURCE (copy from):" "${names[@]}"
    local src="${names[$((REPLY-1))]}"
    
    menu "Mirror TARGET (display on):" "${names[@]}"
    local dst="${names[$((REPLY-1))]}"
    
    if [[ "$src" == "$dst" ]]; then
        warn "Cannot mirror to itself"
        pause
        return
    fi
    
    apply_config "$dst,preferred,auto,1,mirror,$src" "$dst"
}

show_status() {
    clear
    printf '%s%s=== Monitor Status ===%s\n\n' "$C_CYN" "$C_BLD" "$C_R"
    
    local line
    while IFS= read -r line; do
        case "$line" in
            Monitor*) printf '\n%s%s%s\n' "$C_BLD" "$line" "$C_R" ;;
            *vrr:*) 
                if [[ "$line" =~ vrr:[[:space:]]*true ]]; then
                    printf '%s%s%s\n' "$C_GRN" "$line" "$C_R"
                else
                    printf '%s\n' "$line"
                fi
                ;;
            *scale:*|*transform:*|*availableModes*) printf '%s\n' "$line" ;;
            *[0-9]x[0-9]*@*) printf '%s%s%s\n' "$C_GRN" "$line" "$C_R" ;;
            *) printf '%s\n' "$line" ;;
        esac
    done < <(hyprctl monitors)
    
    # Show global settings
    printf '\n%s%s=== Global Settings (misc) ===%s\n' "$C_CYN" "$C_BLD" "$C_R"
    local vfr vrr
    vfr=$(get_misc_option "vfr")
    vrr=$(get_misc_option "vrr")
    
    local vfr_str vrr_str
    (( vfr )) && vfr_str="${C_GRN}true${C_R}" || vfr_str="${C_YLW}false${C_R}"
    
    case $vrr in
        0) vrr_str="${C_DIM}0 (off)${C_R}" ;;
        1) vrr_str="${C_GRN}1 (on)${C_R}" ;;
        2) vrr_str="${C_CYN}2 (fullscreen)${C_R}" ;;
        3) vrr_str="${C_CYN}3 (fullscreen video/game)${C_R}" ;;
        *) vrr_str="$vrr" ;;
    esac
    
    printf '  VFR: %s\n' "$vfr_str"
    printf '  VRR: %s\n' "$vrr_str"
    
    printf '\n%s%s=== Config File ===%s\n' "$C_CYN" "$C_BLD" "$C_R"
    printf '%s%s%s\n\n' "$C_DIM" "$CONFIG_FILE" "$C_R"
    
    if [[ -s "$CONFIG_FILE" ]]; then
        grep -v '^[[:space:]]*#' -- "$CONFIG_FILE" | grep -v '^[[:space:]]*$' || echo "(no active rules)"
    else
        printf '(empty)\n'
    fi
    
    printf '\n'
    pause
}

header() {
    clear
    printf '%s%s╔═══════════════════════════════════════╗%s\n' "$C_CYN" "$C_BLD" "$C_R"
    printf '%s%s║       HyprMonitorWizard v5.6          ║%s\n' "$C_CYN" "$C_BLD" "$C_R"
    printf '%s%s╚═══════════════════════════════════════╝%s\n' "$C_CYN" "$C_BLD" "$C_R"
    printf '%sConfig: %s%s\n\n' "$C_DIM" "$CONFIG_FILE" "$C_R"
    
    printf '%sCurrent Monitors:%s\n' "$C_BLD" "$C_R"
    get_active_json | jq -r '.[] | "  \(.name): \(.width)x\(.height)@\(.refreshRate)Hz (scale \(.scale))"' 2>/dev/null || printf '  (error)\n'
    
    local vfr vrr
    vfr=$(get_misc_option "vfr")
    vrr=$(get_misc_option "vrr")
    printf '\n%sGlobal:%s VFR=%s VRR=%s\n' "$C_DIM" "$C_R" \
        "$( (( vfr )) && printf "on" || printf "off" )" \
        "$vrr"
}

main() {
    check_deps
    
    while :; do
        header
        
        menu "Main Menu:" \
            "Configure Monitor" \
            "Quick Toggle (60Hz ↔ Max)" \
            "Mirror Display" \
            "Global Settings (VFR/VRR)" \
            "Reload Hyprland" \
            "Show Status" \
            "Exit"
        
        case $REPLY in
            1) configure_monitor ;;
            2) quick_toggle ;;
            3) mirror_display ;;
            4) global_settings_menu ;;
            5) 
                hyprctl reload >/dev/null 2>&1 || true
                sleep 1
                clear
                drain_input
                ok "Reloaded"
                sleep 0.5
                ;;
            6) show_status ;;
            7) ok "Bye!"; exit 0 ;;
        esac
    done
}

main "$@"
