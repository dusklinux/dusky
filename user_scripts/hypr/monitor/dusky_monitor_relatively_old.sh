#!/usr/bin/env bash
#══════════════════════════════════════════════════════════════════════════════
# HyprMonitorWizard v7.4.5 — Restored Robustness (Regex, Cleanup, Verbose)
# A robust, strictly typed monitor configuration tool for Hyprland.
#══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

#───────────────────────────────────────────────────────────────────────────────
# CONSTANTS & PATHS
#───────────────────────────────────────────────────────────────────────────────
readonly VERSION="7.4.5"
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hypr/edit_here"
# Backups stored in volatile /tmp (cleared on reboot)
readonly BACKUP_DIR="/tmp/hypr-wizard-backups"
readonly CONFIG_FILE="${CONFIG_DIR}/source/monitors.lua"
readonly MAX_BACKUPS=20

# ANSI Colors & Styling
readonly RST=$'\e[0m'    RED=$'\e[31m'   GRN=$'\e[32m'   YLW=$'\e[33m'
readonly BLU=$'\e[34m'   CYN=$'\e[36m'   BLD=$'\e[1m'    DIM=$'\e[2m'

# State tracking for atomic writes (Restored array-based cleanup)
declare -a TEMP_FILES=()

#───────────────────────────────────────────────────────────────────────────────
# ERROR HANDLING & CLEANUP
#───────────────────────────────────────────────────────────────────────────────
cleanup() {
    # Clean up all registered temp files
    local f
    for f in "${TEMP_FILES[@]}"; do
        [[ -f "$f" ]] && rm -f -- "$f"
    done
}
trap cleanup EXIT

die() { printf '\n%s%s[✖] FATAL: %s%s\n' "$BLD" "$RED" "$1" "$RST" >&2; exit 1; }
warn() { printf '%s%s[!] WARNING: %s%s\n' "$BLD" "$YLW" "$1" "$RST" >&2; }
info() { printf '%s%s[i] %s%s\n' "$DIM" "$BLU" "$1" "$RST" >&2; }
ok()   { printf '%s%s[✔] %s%s\n' "$BLD" "$GRN" "$1" "$RST" >&2; }

# Discard buffered stdin to prevent key-mashing from affecting next prompt
drain_stdin() {
    while read -r -t 0 -n 1; do :; done 2>/dev/null || :
}

pause() {
    printf '\n%sPress [Enter] to continue...%s' "$DIM" "$RST" >&2
    read -r _ || :
}

trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"   # Left trim
    s="${s%"${s##*[![:space:]]}"}"   # Right trim
    printf '%s' "$s"
}

#───────────────────────────────────────────────────────────────────────────────
# DEPENDENCIES & INITIALIZATION
#───────────────────────────────────────────────────────────────────────────────
check_dependencies() {
    if [[ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
        die "Hyprland is not running. This tool requires an active Hyprland session."
    fi

    local -a missing=()
    local cmd
    # Added 'awk' back to checks - it is critical for math logic
    for cmd in jq hyprctl awk; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done

    if (( ${#missing[@]} > 0 )); then
        die "Missing dependencies: ${missing[*]}. Please install them (e.g., sudo pacman -S ${missing[*]})"
    fi

    # Ensure directories exist with explicit error handling
    if ! mkdir -p -- "$BACKUP_DIR" "${CONFIG_FILE%/*}" 2>/dev/null; then
        die "Cannot create required directories. Check permissions for: ${CONFIG_FILE%/*}"
    fi

    # Initialize config if missing
    if [[ ! -f "$CONFIG_FILE" ]]; then
        printf '# HyprMonitorWizard Auto-Generated Config\n# Created: %(%Y-%m-%d)T\n' -1 > "$CONFIG_FILE"
        info "Created new configuration file: $CONFIG_FILE"
    else
        # Ensure it is writable
        touch "$CONFIG_FILE" || die "Config file is not writable: $CONFIG_FILE"
    fi
}

#───────────────────────────────────────────────────────────────────────────────
# INPUT VALIDATION
#───────────────────────────────────────────────────────────────────────────────
validate_resolution() {
    local input="$1"
    [[ "$input" =~ ^(preferred|highres|highrr|maxwidth)$ ]] && return 0
    [[ "$input" =~ ^[0-9]+x[0-9]+(@[0-9]+(\.[0-9]+)?)?$ ]] && return 0
    return 1
}

validate_position() {
    local input="$1"
    # Matches: auto, auto-right, auto-up-left, 0x0, -1920x0
    [[ "$input" =~ ^auto(-(left|right|up|down|center))*$ ]] && return 0
    [[ "$input" =~ ^-?[0-9]+x-?[0-9]+$ ]] && return 0
    return 1
}

validate_scale() {
    local input="$1"
    [[ "$input" == "auto" ]] && return 0
    [[ "$input" =~ ^[0-9]+(\.[0-9]+)?$ ]] && return 0
    return 1
}

#───────────────────────────────────────────────────────────────────────────────
# DATA RETRIEVAL
#───────────────────────────────────────────────────────────────────────────────
get_monitors_json() { hyprctl monitors all -j 2>/dev/null || printf '[]\n'; }
get_active_json()   { hyprctl monitors -j 2>/dev/null || printf '[]\n'; }

get_misc_option() {
    local option="$1" result
    result=$(hyprctl getoption "misc:$option" -j 2>/dev/null) || { printf '0'; return; }
    # robust fallback for int/set/0
    printf '%s' "$result" | jq -r '.int // .set // 0'
}

set_misc_runtime() {
    local option="$1" value="$2"
    hyprctl keyword "misc:$option" "$value" &>/dev/null || :
}

# ══════════════════════════════════════════════════════════════════════════════
# RESTORED: ROBUST UTILS (Regex & Temp Handling)
# ══════════════════════════════════════════════════════════════════════════════
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

make_temp() {
    local tmp
    tmp=$(mktemp) || die "Filesystem error: cannot create temp file"
    TEMP_FILES+=("$tmp")
    printf '%s' "$tmp"
}

#───────────────────────────────────────────────────────────────────────────────
# BACKUP & PERSISTENCE
#───────────────────────────────────────────────────────────────────────────────
create_backup() {
    [[ ! -s "$CONFIG_FILE" ]] && return 0

    local timestamp
    printf -v timestamp '%(%Y%m%d_%H%M%S)T' -1
    cp -- "$CONFIG_FILE" "${BACKUP_DIR}/monitors.${timestamp}.bak"

    # Rotation: Keep last MAX_BACKUPS
    local -a old_backups
    mapfile -t old_backups < <(
        find "$BACKUP_DIR" -maxdepth 1 -name 'monitors.*.bak' -type f -printf '%T@ %p\n' 2>/dev/null |
        sort -rn | tail -n +$((MAX_BACKUPS + 1)) | cut -d' ' -f2-
    )
    
    # Safe iteration
    if (( ${#old_backups[@]} > 0 )); then
        local f
        for f in "${old_backups[@]}"; do rm -f -- "$f"; done
    fi
}

# ══════════════════════════════════════════════════════════════════════════════
# RESTORED: ROBUST SAVE FUNCTION (With Regex Sanitization)
# ══════════════════════════════════════════════════════════════════════════════
save_monitor_rule() {
    local name="$1" rule="$2"
    create_backup

    # Use robust temp creation
    local tmp
    tmp=$(make_temp)

    # RESTORED: Robust Regex Escaping
    local escaped_name
    escaped_name=$(escape_regex "$name")
    
    # CRITICAL: '|| true' prevents set -e from killing script if grep finds nothing
    grep -v "^[[:space:]]*monitor[[:space:]]*=[[:space:]]*${escaped_name}[,[:space:]]" \
        -- "$CONFIG_FILE" > "$tmp" 2>/dev/null || true

    printf '%s\n' "$rule" >> "$tmp"
    mv -- "$tmp" "$CONFIG_FILE"
    ok "Configuration saved."
}

# ══════════════════════════════════════════════════════════════════════════════
# RESTORED: ROBUST MISC SAVE (With Temp Handling)
# ══════════════════════════════════════════════════════════════════════════════
save_misc_option() {
    local option="$1" value="$2"
    create_backup

    local tmp in_misc=0 found=0 line
    tmp=$(make_temp)

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
        cat -- "$CONFIG_FILE" > "$tmp"
        printf '\n# Global Settings\nmisc {\n    %s = %s\n}\n' "$option" "$value" >> "$tmp"
    fi

    mv -- "$tmp" "$CONFIG_FILE"
    ok "Global setting saved: misc:$option = $value"
}
# ══════════════════════════════════════════════════════════════════════════════

#───────────────────────────────────────────────────────────────────────────────
# UI COMPONENTS
#───────────────────────────────────────────────────────────────────────────────
print_header() {
    local title="${1:-Main Menu}"
    clear
    printf '\n%s%s╔══════════════════════════════════════════════════╗%s\n' "$CYN" "$BLD" "$RST"
    printf '%s%s║   🖥️  HyprMonitorWizard v%-4s                   ║%s\n' "$CYN" "$BLD" "$VERSION" "$RST"
    printf '%s%s╚══════════════════════════════════════════════════╝%s\n' "$CYN" "$BLD" "$RST"
    printf '  %s📍 %s%s\n\n' "$DIM" "$title" "$RST"
}

menu() {
    local prompt="$1"; shift
    local -a options=("$@")
    local input idx

    drain_stdin
    printf '%s%s%s\n' "$BLD" "$prompt" "$RST" >&2
    printf '%s────────────────────────────────────────────────────%s\n' "$DIM" "$RST" >&2

    for idx in "${!options[@]}"; do
        printf '  %s%2d%s │ %s\n' "$CYN" "$((idx + 1))" "$RST" "${options[idx]}" >&2
    done
    printf '  %s 0 │ Back / Cancel%s\n' "$DIM" "$RST" >&2
    printf '%s────────────────────────────────────────────────────%s\n' "$DIM" "$RST" >&2

    while :; do
        printf '%s►%s ' "$CYN" "$RST" >&2
        IFS= read -r input || input=""
        input=$(trim "$input")

        [[ "$input" == "0" || "$input" == "q" ]] && { REPLY=0; return 0; }
        [[ -z "$input" ]] && continue

        if [[ "$input" =~ ^[0-9]+$ ]] && (( input >= 1 && input <= ${#options[@]} )); then
            REPLY="$input"
            return 0
        fi
        printf '  %sInvalid selection. Enter 0-%d%s\n' "$RED" "${#options[@]}" "$RST" >&2
    done
}

prompt_input() {
    local prompt="$1" default="${2:-}" validator="${3:-}"
    local input

    while :; do
        if [[ -n "$default" ]]; then
            printf '%s [%s]: ' "$prompt" "$default" >&2
        else
            printf '%s: ' "$prompt" >&2
        fi
        IFS= read -r input || input=""
        input=$(trim "$input")

        [[ -z "$input" && -n "$default" ]] && input="$default"
        [[ -z "$input" ]] && continue

        if [[ -n "$validator" ]] && ! "$validator" "$input"; then
            warn "Invalid format."
            continue
        fi
        printf '%s' "$input"
        return 0
    done
}

confirm() {
    local msg="${1:-Continue?}" reply
    printf '%s%s%s [y/N]: ' "$YLW" "$msg" "$RST" >&2
    IFS= read -r reply || reply=""
    reply=$(trim "$reply")
    [[ "${reply,,}" == y || "${reply,,}" == yes ]]
}

#───────────────────────────────────────────────────────────────────────────────
# CORE LOGIC: MONITOR CONFIGURATION
#───────────────────────────────────────────────────────────────────────────────
get_position() {
    local current_mon="$1"
    local new_res="$2" new_scale="$3" new_transform="$4"
    local json n
    local -a anchors=()

    json=$(get_active_json)
    local -a names_raw
    mapfile -t names_raw < <(printf '%s' "$json" | jq -r '.[].name')

    # Filter out current monitor from potential anchors
    for n in "${names_raw[@]}"; do
        [[ "$n" != "$current_mon" ]] && anchors+=("$n")
    done

    # If no OTHER monitors exist, this is the only one. Auto-return 0x0.
    if (( ${#anchors[@]} == 0 )); then
        printf '0x0'
        return
    fi

    local anchor="${anchors[0]}"
    if (( ${#anchors[@]} > 1 )); then
        menu "Position relative to which monitor?" "${anchors[@]}"
        (( REPLY == 0 )) && { printf 'auto'; return; }
        anchor="${anchors[$((REPLY-1))]}"
    fi

    # Parse anchor geometry
    local ax ay aw ah ascale atransform
    IFS=' ' read -r ax ay aw ah ascale atransform < <(
        printf '%s' "$json" | jq -r --arg n "$anchor" \
            '.[] | select(.name==$n) | "\(.x) \(.y) \(.width) \(.height) \(.scale) \(.transform)"'
    )

    # Parse new monitor geometry
    local nw nh
    if [[ "$new_res" =~ ([0-9]+)x([0-9]+) ]]; then
        nw="${BASH_REMATCH[1]}"
        nh="${BASH_REMATCH[2]}"
    else
        nw=1920; nh=1080 # Fallback
    fi
    
    local nscale_calc="$new_scale"
    [[ "$nscale_calc" == "auto" ]] && nscale_calc=1

    # Calculate logical dimensions with rotation swapping (transform % 2 != 0)
    local alw alh nlw nlh
    
    IFS=' ' read -r alw alh < <(
        awk -v w="$aw" -v h="$ah" -v s="$ascale" -v t="$atransform" 'BEGIN {
            if (t % 2 != 0) { tmp=w; w=h; h=tmp }
            printf "%.0f %.0f", w/s, h/s 
        }'
    )

    IFS=' ' read -r nlw nlh < <(
        awk -v w="$nw" -v h="$nh" -v s="$nscale_calc" -v t="$new_transform" 'BEGIN {
            if (t % 2 != 0) { tmp=w; w=h; h=tmp }
            printf "%.0f %.0f", w/s, h/s 
        }'
    )

    menu "Position relative to $anchor:" \
        "Right of $anchor" \
        "Left of $anchor" \
        "Above $anchor" \
        "Below $anchor" \
        "Mirror (Same position)" \
        "Custom coordinates"

    # Convert to integers for arithmetic
    local iax iay ialw ialh inlw inlh
    printf -v iax '%.0f' "$ax"
    printf -v iay '%.0f' "$ay"
    printf -v ialw '%.0f' "$alw"
    printf -v ialh '%.0f' "$alh"
    printf -v inlw '%.0f' "$nlw"
    printf -v inlh '%.0f' "$nlh"

    case $REPLY in
        0) printf 'auto' ;;
        1) printf '%dx%d' "$((iax + ialw))" "$iay" ;;
        2) printf '%dx%d' "$((iax - inlw))" "$iay" ;;
        3) printf '%dx%d' "$iax" "$((iay - inlh))" ;;
        4) printf '%dx%d' "$iax" "$((iay + ialh))" ;;
        5) printf '%dx%d' "$iax" "$iay" ;;
        6) prompt_input "Enter position (e.g. 1920x0)" "0x0" validate_position ;;
    esac
}

apply_monitor_config() {
    local monitor_name="$1" config_string="$2"
    
    # ══════════════════════════════════════════════════════════════════════════
    # RESTORED: VERBOSE ANALYSIS (Before/After)
    # ══════════════════════════════════════════════════════════════════════════
    local before_hz before_scale after_hz after_scale
    
    # Capture state BEFORE
    IFS=' ' read -r before_hz before_scale < <(
        get_active_json | jq -r --arg n "$monitor_name" \
            '.[] | select(.name==$n) | "\(.refreshRate) \(.scale)"'
    )
    
    print_header "Applying Configuration"
    printf '%sTarget Rule:%s monitor = %s\n\n' "$DIM" "$RST" "$config_string"
    printf '%sCurrent State:%s %sHz, Scale: %s\n\n' "$DIM" "$RST" "${before_hz:-?}" "${before_scale:-?}"

    if confirm "Write configuration to file and Apply? (Screen will flash)"; then
        # Save to disk
        save_monitor_rule "$monitor_name" "monitor = $config_string"
        
        info "Applying changes..."
        hyprctl keyword monitor "$config_string" &>/dev/null || :
        
        # Give Hyprland a moment to adjust
        sleep 0.5
        
        # Capture state AFTER
        IFS=' ' read -r after_hz after_scale < <(
            get_active_json | jq -r --arg n "$monitor_name" \
                '.[] | select(.name==$n) | "\(.refreshRate) \(.scale)"'
        )
        
        # VERBOSE REPORT
        printf '\n%s%s=== Configuration Report ===%s\n' "$CYN" "$BLD" "$RST"
        printf '  Monitor: %s\n' "$monitor_name"
        printf '  Refresh: %s -> %s%s%s\n' \
            "${before_hz:-?}" "$GRN" "${after_hz:-?}" "$RST"
        printf '  Scale:   %s -> %s%s%s\n' \
            "${before_scale:-?}" "$GRN" "${after_scale:-?}" "$RST"
            
        if [[ "$before_hz" == "$after_hz" && "$before_scale" == "$after_scale" ]]; then
            warn "Settings appear unchanged. (This is normal if the monitor was already optimized)."
        else
            ok "Settings applied successfully."
        fi
        
        pause
    else
        warn "Operation cancelled. No changes made."
        pause
    fi
}

configure_monitor() {
    local json mon m
    
    json=$(get_monitors_json)
    local -a names
    mapfile -t names < <(printf '%s' "$json" | jq -r '.[].name')

    if (( ${#names[@]} == 0 )); then
        warn "No monitors found."
        pause; return
    fi

    # Build menu labels
    local -a labels=()
    local n
    for n in "${names[@]}"; do
        labels+=("$(printf '%s' "$json" | jq -r --arg n "$n" \
            '.[] | select(.name==$n) | "\(.name): \(.width)x\(.height)@\(.refreshRate | floor)Hz"')")
    done

    menu "Select Monitor to Configure:" "${labels[@]}"
    (( REPLY == 0 )) && return

    local name="${names[$((REPLY-1))]}"
    mon=$(printf '%s' "$json" | jq --arg n "$name" '.[] | select(.name==$n)')

    # --- Step 1: Resolution ---
    local -a modes_raw
    mapfile -t modes_raw < <(printf '%s' "$mon" | jq -r '.availableModes[]?' 2>/dev/null | sort -t@ -k2 -rn -u)

    local max_mode="preferred" low_mode="preferred"
    if (( ${#modes_raw[@]} > 0 )); then
        max_mode="${modes_raw[0]}"
        for m in "${modes_raw[@]}"; do
            if [[ "$m" =~ (59|60|61)\. ]]; then low_mode="$m"; break; fi
        done
        [[ "$low_mode" == "preferred" ]] && low_mode="${modes_raw[-1]}"
    fi

    # --- LIST MODES (Restored) ---
    printf '\n%sAvailable modes for %s:%s\n' "$CYN" "$name" "$RST" >&2
    if (( ${#modes_raw[@]} > 0 )); then
        local idx=1
        for m in "${modes_raw[@]}"; do
            printf '  %2d) %s\n' "$idx" "$m" >&2
            ((idx++))
        done
        printf '\n' >&2
    else
        printf '  %s(No specific modes reported by Hyprland)%s\n\n' "$DIM" "$RST" >&2
    fi

    menu "Resolution & Refresh ($name):" \
        "Preferred (Auto)" \
        "Max Refresh ($max_mode)" \
        "Power Save 60Hz ($low_mode)" \
        "Custom" \
        "Disable Monitor"

    local res
    case $REPLY in
        0) return ;;
        1) res="preferred" ;;
        2) res="$max_mode" ;;
        3) res="$low_mode" ;;
        4) res=$(prompt_input "Mode (e.g. 1920x1080@144)" "preferred" validate_resolution) ;;
        5)
            if confirm "Disable $name?"; then apply_monitor_config "$name" "$name,disable"; fi
            return ;;
    esac

    res="${res//Hz/}"

    # Resolve preferred for math
    local res_math="$res"
    [[ "$res" == "preferred" && "$max_mode" != "preferred" ]] && res_math="$max_mode"

    # --- Step 2: Scale ---
    local cur_scale
    cur_scale=$(printf '%s' "$mon" | jq -r '.scale // 1')

    menu "Scale Factor:" "1 (Native)" "1.25" "1.5" "2 (HiDPI)" "Auto" "Keep Current ($cur_scale)" "Custom"
    local scale
    case $REPLY in
        0) return ;;
        1) scale="1" ;;
        2) scale="1.25" ;;
        3) scale="1.5" ;;
        4) scale="2" ;;
        5) scale="auto" ;;
        6) scale="$cur_scale" ;;
        7) scale=$(prompt_input "Scale" "1" validate_scale) ;;
    esac

    # --- Step 3: Rotation ---
    menu "Rotation:" "Normal (0°)" "90° (Vertical)" "180° (Inverted)" "270° (Vertical)"
    (( REPLY == 0 )) && return
    local transform=$((REPLY - 1))

    # --- Step 4: Position ---
    local position
    position=$(get_position "$name" "$res_math" "$scale" "$transform")

    # --- Assemble ---
    local cmd="$name,$res,$position,$scale"
    (( transform > 0 )) && cmd+=",transform,$transform"

    if confirm "Enable VRR (Adaptive Sync) for this monitor?"; then
        cmd+=",vrr,1"
    fi

    apply_monitor_config "$name" "$cmd"
}

#───────────────────────────────────────────────────────────────────────────────
# ADDITIONAL FEATURES
#───────────────────────────────────────────────────────────────────────────────
quick_toggle() {
    local json name current_hz current_x current_y current_scale
    json=$(get_active_json)
    
    if [[ "$json" == "[]" ]]; then warn "No monitors."; pause; return; fi

    IFS=' ' read -r name current_hz current_x current_y current_scale < <(
        printf '%s' "$json" | jq -r '.[0] | "\(.name) \(.refreshRate) \(.x) \(.y) \(.scale)"'
    )

    local -a modes_raw
    mapfile -t modes_raw < <(get_monitors_json | jq -r --arg n "$name" \
        '.[] | select(.name==$n) | .availableModes[]?' 2>/dev/null | sort -t@ -k2 -rn)

    local max_mode="${modes_raw[0]:-preferred}"
    local low_mode="" m
    for m in "${modes_raw[@]}"; do
        if [[ "$m" =~ (59|60|61)\. ]]; then low_mode="$m"; break; fi
    done
    [[ -z "$low_mode" ]] && low_mode="${modes_raw[-1]:-preferred}"

    local target_mode
    local hz_int
    printf -v hz_int '%.0f' "$current_hz"

    if (( hz_int > 65 )); then
        target_mode="$low_mode"
        info "Switching to: Power Save (60Hz)"
    else
        target_mode="$max_mode"
        info "Switching to: Max Performance"
    fi

    target_mode="${target_mode//Hz/}"

    apply_monitor_config "$name" "$name,$target_mode,${current_x%%.*}x${current_y%%.*},$current_scale"
}

mirror_display() {
    local json; json=$(get_monitors_json)
    local -a names; mapfile -t names < <(printf '%s' "$json" | jq -r '.[].name')

    if (( ${#names[@]} < 2 )); then warn "Need 2+ monitors to mirror."; pause; return; fi

    menu "Select Source (Content Provider):" "${names[@]}"
    (( REPLY == 0 )) && return
    local src="${names[$((REPLY-1))]}"

    menu "Select Target (Mirror Display):" "${names[@]}"
    (( REPLY == 0 )) && return
    local dst="${names[$((REPLY-1))]}"

    if [[ "$src" == "$dst" ]]; then warn "Cannot mirror self."; pause; return; fi

    apply_monitor_config "$dst" "$dst,preferred,auto,1,mirror,$src"
}

#───────────────────────────────────────────────────────────────────────────────
# GLOBAL SETTINGS
#───────────────────────────────────────────────────────────────────────────────
global_settings_menu() {
    while :; do
        print_header "Global Settings"
        local vfr vrr vfr_str vrr_str
        vfr=$(get_misc_option "vfr")
        vrr=$(get_misc_option "vrr")

        (( vfr )) && vfr_str="${GRN}Enabled${RST}" || vfr_str="${YLW}Disabled${RST}"
        case $vrr in
            0) vrr_str="${DIM}Off${RST}" ;;
            1) vrr_str="${GRN}On (All)${RST}" ;;
            2) vrr_str="${CYN}Fullscreen${RST}" ;;
            *) vrr_str="${RED}Unknown${RST}" ;;
        esac

        printf '%sCurrent Status:%s\n' "$BLD" "$RST"
        printf '  VFR: %s\n  VRR: %s\n\n' "$vfr_str" "$vrr_str"

        menu "Options:" "Toggle VFR (Power Save)" "Configure Global VRR"
        case $REPLY in
            0) return ;;
            1) 
                local new_val="true" desc="Enabled"
                (( vfr )) && { new_val="false"; desc="Disabled"; }
                set_misc_runtime "vfr" "$new_val"
                if confirm "Save VFR=$desc to config?"; then save_misc_option "vfr" "$new_val"; fi
                ;;
            2)
                menu "Global VRR:" "Off" "On (All)" "Fullscreen Only" "Fullscreen+Game"
                (( REPLY == 0 )) && continue
                local val=$((REPLY - 1))
                set_misc_runtime "vrr" "$val"
                if confirm "Save VRR setting to config?"; then save_misc_option "vrr" "$val"; fi
                ;;
        esac
    done
}

show_status() {
    print_header "System Status"
    local json mon_count
    
    printf '%sActive Monitors:%s\n' "$BLD" "$RST"
    json=$(get_active_json)
    mon_count=$(printf '%s' "$json" | jq 'length')
    
    if (( mon_count == 0 )); then
        printf '  (None)\n'
    else
        printf '%s' "$json" | jq -r '.[] | "  \(.name): \(.width)x\(.height)@\(.refreshRate | floor)Hz at \(.x),\(.y) scale=\(.scale)"'
    fi

    printf '\n%sConfig File (%s):%s\n' "$BLD" "$CONFIG_FILE" "$RST"
    if [[ -s "$CONFIG_FILE" ]]; then
        grep -v '^[[:space:]]*#' "$CONFIG_FILE" | grep -v '^[[:space:]]*$' || printf '  (Empty)\n'
    else
        printf '  (File missing or empty)\n'
    fi
    pause
}

#───────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY
#───────────────────────────────────────────────────────────────────────────────
main() {
    check_dependencies
    while :; do
        print_header "Main Menu"
        menu "Select Operation:" \
            "Configure Monitor" \
            "Quick Toggle (60Hz ↔ Max)" \
            "Mirror Display" \
            "Global Settings (VFR/VRR)" \
            "Reload Hyprland" \
            "Show Status" \
            "Exit"

        case $REPLY in
            0|7) ok "Exiting."; exit 0 ;;
            1) configure_monitor ;;
            2) quick_toggle ;;
            3) mirror_display ;;
            4) global_settings_menu ;;
            5) hyprctl reload &>/dev/null; ok "Reloaded."; sleep 1 ;;
            6) show_status ;;
        esac
    done
}

main "$@"
