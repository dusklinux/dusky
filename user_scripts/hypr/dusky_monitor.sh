#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky Monitor Wizard - Hyprland Edition v4.0.0 (Template Synced)
# -----------------------------------------------------------------------------
# A pure Bash TUI for Hyprland monitor management.
#
# v4.0.0 CHANGELOG:
#   - REFACTOR: Full TUI engine replacement based on dusky_tui.sh v3.9.5.
#   - FEAT: Added Sliding Tabs logic (future-proofs UI).
#   - FEAT: Added 'g' (top), 'G' (bottom), PageUp/Down navigation.
#   - FIX: Atomic save now uses 'cat > target' to preserve symlinks/inodes.
#   - FIX: Added empty-file checks before saving (Data Loss Prevention).
#   - FIX: Strict arithmetic guards for 'set -e' safety.
#   - VISUAL: Added ellipsis (…) for long monitor names.
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s extglob

# Force C locale for consistent float handling (dot vs comma) inside internal logic
export LC_NUMERIC=C

# =============================================================================
# ▼ CONFIGURATION ▼
# =============================================================================

declare -r APP_TITLE="DUSKY MONITOR WIZARD v4.0.0"
declare -r APP_SUBTITLE="Hyprland Edition"
declare -r TARGET_CONFIG="${HOME}/.config/hypr/edit_here/source/monitors.conf"
declare -r BACKUP_DIR="/tmp/dusky_backups"
declare -r DEBUG_LOG="/tmp/dusky_debug.log"

# Dimensions & Layout (Synced with Template standards)
declare -ri BOX_INNER_WIDTH=76
declare -ri MAX_DISPLAY_ROWS=12
declare -ri TAB_ROW=2
declare -ri ITEM_START_ROW=5
declare -ri ITEM_PADDING=32 

declare -r ESC_READ_TIMEOUT=0.10

readonly -a TRANSFORMS=("Normal" "90°" "180°" "270°" "Flipped" "Flipped-90°" "Flipped-180°" "Flipped-270°")
readonly -a ANCHOR_POSITIONS=("Absolute" "Right Of" "Left Of" "Above" "Below" "Mirror")

declare -ra TABS=("Monitors" "Globals")
declare -ri TAB_COUNT=${#TABS[@]}

# =============================================================================
# ▼ ANSI CONSTANTS ▼
# =============================================================================

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

# =============================================================================
# ▼ PRE-COMPUTED CONSTANTS ▼
# =============================================================================

declare _box_line_buf
printf -v _box_line_buf '%*s' "$(( BOX_INNER_WIDTH + 2 ))" ''
declare -r BOX_LINE="${_box_line_buf// /─}"
unset _box_line_buf

# =============================================================================
# ▼ CLEANUP & SAFETY ▼
# =============================================================================

ORIG_STTY=""
_SAVE_TMPFILE=""

log_debug() {
    printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$1" >> "$DEBUG_LOG"
}

log_err() {
    printf '[ERROR] %s\n' "$1" >> "$DEBUG_LOG"
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
}

cleanup() {
    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    if [[ -n "${ORIG_STTY:-}" ]]; then
        stty "$ORIG_STTY" 2>/dev/null || :
    fi
    if [[ -n "${_SAVE_TMPFILE:-}" && -f "$_SAVE_TMPFILE" ]]; then
        rm -f -- "$_SAVE_TMPFILE" 2>/dev/null || :
    fi
    printf '\n' 2>/dev/null || :
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# =============================================================================
# ▼ STATE MANAGEMENT ▼
# =============================================================================

declare -i GLB_VFR=1
declare -i GLB_USE_DESC=0

declare -a MON_LIST=()
declare -A MON_ENABLED=()
declare -A MON_DESC=()
declare -A MON_CUR_RES=()   # e.g., "1920x1080"
declare -A MON_CUR_RATE=()  # e.g., "143.88" (Exact)
declare -A MON_SCALE=()
declare -A MON_TRANSFORM=()
declare -A MON_X=()
declare -A MON_Y=()
declare -A MON_VRR=()
declare -A MON_BITDEPTH=()
declare -A MON_MIRROR=()

declare -A UI_ANCHOR_TARGET=()
declare -A UI_ANCHOR_MODE=()
declare -A MON_RAW_MODES=()

# UI State
declare -i CURRENT_TAB=0
declare -i CURRENT_VIEW=0  # 0=MonList/GlobalList, 1=Edit, 2=Picker
declare -i SCROLL_OFFSET=0
declare -i SELECTED_ROW=0
declare -i LIST_SAVED_ROW=0
declare CURRENT_MON=""

# Sliding Tabs State (From Template)
declare -a TAB_ZONES=()
declare -i TAB_SCROLL_START=0
declare LEFT_ARROW_ZONE=""
declare RIGHT_ARROW_ZONE=""

declare -a PICKER_LIST=()
declare -i PICKER_SCROLL=0
declare -i PICKER_ROW=0
declare PICKER_TYPE="" # "RES" (Hybrid) or "RATE" (Buckets)

declare -i GEO_W=0
declare -i GEO_H=0

# =============================================================================
# ▼ UTILITY FUNCTIONS ▼
# =============================================================================

# Robust ANSI stripping from Template
strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
    REPLY="$v"
}

float_lt() {
    awk -v a="$1" -v b="$2" 'BEGIN { exit !(a < b) }'
}

float_add() {
    REPLY=$(awk -v a="$1" -v b="$2" 'BEGIN { printf "%.2f", a + b }')
}

float_round_int() {
    awk -v a="$1" 'BEGIN { printf "%.0f", a }'
}

snap_refresh_rate() {
    local raw_rate="$1"
    local raw_modes="$2"
    
    local -a candidates
    mapfile -t candidates < <(printf '%s' "$raw_modes" | sed 's/ /\n/g' | grep '@' | sed -E 's/.*@([0-9.]+).*/\1/' | sort -uV)

    if (( ${#candidates[@]} == 0 )); then
        printf -v REPLY "%.2f" "$raw_rate"
        return
    fi

    local best="${candidates[0]}"
    local min_diff=999999

    for c in "${candidates[@]}"; do
        local diff
        diff=$(awk -v a="$raw_rate" -v b="$c" 'BEGIN { d = a - b; if (d < 0) d = -d; print d }')
        if (( $(awk -v d="$diff" -v m="$min_diff" 'BEGIN { print (d < m) }') )); then
            min_diff="$diff"
            best="$c"
        fi
    done
    
    if (( $(awk -v d="$min_diff" 'BEGIN { print (d < 1.5) }') )); then
        REPLY="$best"
    else
        printf -v REPLY "%.2f" "$raw_rate"
    fi
}

# =============================================================================
# ▼ BACKEND LOGIC ▼
# =============================================================================

refresh_hardware() {
    log_debug "Refreshing hardware..."

    # xrandr is optional; removed from fatal check
    for _cmd in hyprctl jq awk stty; do
        if ! command -v "$_cmd" &>/dev/null; then
            printf 'FATAL: Required command "%s" not found in PATH.\n' "$_cmd" >&2
            exit 1
        fi
    done

    local vfr_status
    vfr_status=$(hyprctl getoption misc:vfr -j 2>/dev/null | jq -r '.int' 2>/dev/null) || vfr_status="1"
    GLB_VFR=$vfr_status

    local json
    json=$(hyprctl monitors all -j 2>/dev/null) || {
        log_err "Failed to query hyprctl monitors."
        exit 1
    }

    MON_LIST=()
    local extracted
    extracted=$(printf '%s' "$json" | jq -r '
        .[] | [
            .name,
            .description,
            (.disabled // false | tostring),
            (.width | tostring),
            (.height | tostring),
            (.refreshRate | tostring),
            (.scale | tostring),
            (.transform | tostring),
            (.x | tostring),
            (.y | tostring),
            ((.availableModes // []) | join(" ")),
            (.vrr // false | tostring),
            (.currentFormat // "XRGB8888")
        ] | @tsv
    ') || true

    if [[ -z "$extracted" ]]; then
        log_err "No monitors detected."
        exit 1
    fi

    local name desc disabled width height refresh scale transform x y avail_modes vrr_bool fmt
    while IFS=$'\t' read -r name desc disabled width height refresh scale transform x y avail_modes vrr_bool fmt; do
        MON_LIST+=("$name")
        MON_DESC["$name"]="$desc"
        MON_ENABLED["$name"]="${disabled/false/true}"
        [[ "$disabled" == "true" ]] && MON_ENABLED["$name"]="false"

        MON_CUR_RES["$name"]="${width}x${height}"
        MON_RAW_MODES["$name"]="$avail_modes"

        snap_refresh_rate "$refresh" "$avail_modes"
        MON_CUR_RATE["$name"]="$REPLY"
        
        MON_SCALE["$name"]="$scale"
        MON_TRANSFORM["$name"]="$transform"
        MON_X["$name"]="$x"
        MON_Y["$name"]="$y"

        if [[ "$vrr_bool" == "true" ]]; then
            MON_VRR["$name"]="1"
        else
            MON_VRR["$name"]="0"
        fi

        if [[ "$fmt" == *"101010"* ]]; then
            MON_BITDEPTH["$name"]="10"
        else
            MON_BITDEPTH["$name"]="8"
        fi

        MON_MIRROR["$name"]=""
        UI_ANCHOR_MODE["$name"]="0"
        UI_ANCHOR_TARGET["$name"]=""
    done <<< "$extracted"
}

get_logical_geometry() {
    local name=$1
    local res_str=${MON_CUR_RES["$name"]}
    local width=${res_str%%x*}
    local height=${res_str#*x}
    local scale=${MON_SCALE["$name"]}
    local t=${MON_TRANSFORM["$name"]}

    case "$t" in
        1|3|5|7)
            local tmp=$width
            width=$height
            height=$tmp
            ;;
    esac

    GEO_W=$(awk -v w="$width" -v s="$scale" 'BEGIN { printf "%.0f", w / s }')
    GEO_H=$(awk -v h="$height" -v s="$scale" 'BEGIN { printf "%.0f", h / s }')
}

recalc_position() {
    local name=$1
    local mode=${UI_ANCHOR_MODE["$name"]}
    local target=${UI_ANCHOR_TARGET["$name"]}

    if (( mode == 0 )); then return; fi
    if [[ -z "$target" || "$target" == "$name" ]]; then
        UI_ANCHOR_MODE["$name"]=0
        return
    fi

    local -i t_x=${MON_X["$target"]}
    local -i t_y=${MON_Y["$target"]}

    get_logical_geometry "$target"
    local -i t_w=$GEO_W t_h=$GEO_H

    get_logical_geometry "$name"
    local -i s_w=$GEO_W s_h=$GEO_H

    case "$mode" in
        1) MON_X["$name"]=$(( t_x + t_w )); MON_Y["$name"]=$t_y ;;
        2) MON_X["$name"]=$(( t_x - s_w )); MON_Y["$name"]=$t_y ;;
        3) MON_X["$name"]=$t_x; MON_Y["$name"]=$(( t_y - s_h )) ;;
        4) MON_X["$name"]=$t_x; MON_Y["$name"]=$(( t_y + t_h )) ;;
        5) MON_X["$name"]=$t_x; MON_Y["$name"]=$t_y; MON_MIRROR["$name"]="$target" ;;
    esac

    if (( mode != 5 )); then
        MON_MIRROR["$name"]=""
    fi
}

save_config() {
    log_debug "Saving configuration..."
    local config_dir="${TARGET_CONFIG%/*}"
    mkdir -p "$config_dir" "$BACKUP_DIR"

    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)

    if [[ -f "$TARGET_CONFIG" ]]; then
        cp -- "$TARGET_CONFIG" "${BACKUP_DIR}/monitors_${timestamp}.conf" 2>/dev/null || true
    fi

    _SAVE_TMPFILE=$(mktemp "${config_dir}/monitors.conf.XXXXXX")

    local batch_cmd=""

    {
        printf '# Generated by Dusky Monitor Wizard on %s\n' "$timestamp"
        printf '\n# Global Settings\n'

        if (( GLB_VFR == 1 )); then
            printf 'misc {\n    vfr = true\n}\n'
            batch_cmd+="keyword misc:vfr 1 ; "
        else
            printf 'misc {\n    vfr = false\n}\n'
            batch_cmd+="keyword misc:vfr 0 ; "
        fi

        printf '\n# Monitor Rules\n'
        local name
        for name in "${MON_LIST[@]}"; do
            local identifier="$name"
            if (( GLB_USE_DESC == 1 )); then
                identifier="desc:${MON_DESC["$name"]}"
            fi

            if [[ "${MON_ENABLED["$name"]}" == "false" ]]; then
                printf 'monitor = %s, disable\n' "$identifier"
                batch_cmd+="keyword monitor ${identifier},disable ; "
                continue
            fi

            local res="${MON_CUR_RES["$name"]}@${MON_CUR_RATE["$name"]}"
            local x=${MON_X["$name"]}
            local y=${MON_Y["$name"]}
            local scale=${MON_SCALE["$name"]}
            local transform=${MON_TRANSFORM["$name"]}
            local vrr=${MON_VRR["$name"]}
            local bit=${MON_BITDEPTH["$name"]}
            local mirror=${MON_MIRROR["$name"]}

            local rule_args="${identifier}, ${res}, ${x}x${y}, ${scale}"
            (( transform != 0 )) && rule_args+=", transform, ${transform}"
            [[ -n "$mirror" ]] && rule_args+=", mirror, ${mirror}"
            (( bit == 10 )) && rule_args+=", bitdepth, 10"
            (( vrr > 0 )) && rule_args+=", vrr, ${vrr}"

            printf 'monitor = %s\n' "$rule_args"
            local clean_args="${rule_args//, /,}"
            batch_cmd+="keyword monitor ${clean_args} ; "
        done
    } > "$_SAVE_TMPFILE"

    # CRITICAL FIX: Verify temp file integrity
    if [[ ! -s "$_SAVE_TMPFILE" ]]; then
        log_err "Generated config file is empty. Aborting save."
        rm -f -- "$_SAVE_TMPFILE" 2>/dev/null || :
        _SAVE_TMPFILE=""
        return 1
    fi

    # CRITICAL FIX: Use cat > target to preserve symlinks/inodes (Pattern from Template)
    if cat "$_SAVE_TMPFILE" > "$TARGET_CONFIG"; then
        printf '\n%sApplying settings...%s\n' "$C_CYAN" "$C_RESET"
        if [[ -n "$batch_cmd" ]]; then
            hyprctl --batch "$batch_cmd" >/dev/null 2>&1 || true
        fi
        log_debug "Saved and applied."
        rm -f -- "$_SAVE_TMPFILE" 2>/dev/null || :
        _SAVE_TMPFILE=""
    else
        log_err "Failed to write to target config file."
        rm -f -- "$_SAVE_TMPFILE" 2>/dev/null || :
        _SAVE_TMPFILE=""
    fi
}

# =============================================================================
# ▼ HYBRID MODE PARSING ▼
# =============================================================================

get_available_rate_buckets() {
    local mon="$1"
    local raw="${MON_RAW_MODES["$mon"]}"
    printf '%s' "$raw" | sed 's/ /\n/g' | grep '@' | \
    sed -E 's/.*@([0-9.]+).*/\1/' | \
    awk '{ printf "%.0f\n", $1 }' | sort -uVr
}

get_resolutions_for_bucket() {
    local mon="$1"
    local target_bucket="$2"
    local raw="${MON_RAW_MODES["$mon"]}"
    printf '%s' "$raw" | sed 's/ /\n/g' | \
    awk -F'@' -v bucket="$target_bucket" '{
        sub(/Hz/, "", $2); 
        if (sprintf("%.0f", $2) == bucket) print $1
    }' | sort -uVr
}

find_exact_rate_in_bucket() {
    local mon="$1"
    local res="$2"
    local bucket="$3"
    local raw="${MON_RAW_MODES["$mon"]}"
    local best_rate=""
    local mode
    for mode in $raw; do
        if [[ "$mode" == "${res}@"* ]]; then
            local r_str="${mode#*@}"
            r_str="${r_str%Hz}"
            local r_int
            r_int=$(float_round_int "$r_str")
            if (( r_int == bucket )); then
                best_rate="$r_str"
                break 
            fi
        fi
    done
    if [[ -z "$best_rate" ]]; then
        best_rate="${MON_CUR_RATE["$mon"]}"
    fi
    REPLY="$best_rate"
}

get_all_modes_combined() {
    local mon="$1"
    local raw_hypr="${MON_RAW_MODES["$mon"]}"
    
    local -a modes
    mapfile -t modes < <(printf '%s' "$raw_hypr" | sed 's/ /\n/g' | grep '@' | sed 's/@/ @ /')

    if command -v xrandr &>/dev/null; then
        local x_modes
        x_modes=$(xrandr --query 2>/dev/null | awk -v mon="$mon" '
            $1 == mon && $2 == "connected" { in_block = 1; next }
            $1 ~ /^[a-zA-Z]/ && $1 != mon { in_block = 0 }
            in_block && $1 ~ /^[0-9]+x[0-9]+$/ {
                res = $1
                for (i=2; i<=NF; i++) {
                    rate = $i
                    gsub(/[\*\+]/, "", rate)
                    if (rate != "") {
                        printf "%s @ %sHz\n", res, rate
                    }
                }
            }
        ')
        if [[ -n "$x_modes" ]]; then
            mapfile -t -O "${#modes[@]}" modes <<< "$x_modes"
        fi
    fi

    printf '%s\n' "${modes[@]}" | sort -uVr
}

# =============================================================================
# ▼ TUI ENGINE (DRAWING) ▼
# =============================================================================

draw_box_top() { printf '%s┌%s┐%s\n' "$C_MAGENTA" "$BOX_LINE" "$C_RESET"; }
draw_box_bottom() { printf '%s└%s┘%s\n' "$C_MAGENTA" "$BOX_LINE" "$C_RESET"; }
draw_separator() { printf '%s├%s┤%s\n' "$C_MAGENTA" "$BOX_LINE" "$C_RESET"; }

draw_row() {
    local content="${1:-}"
    strip_ansi "$content"
    local -i vis_len=${#REPLY}
    local -i pad_needed=$(( BOX_INNER_WIDTH - vis_len ))
    (( pad_needed < 0 )) && pad_needed=0
    printf '%s│%s %s%*s %s│%s\n' "$C_MAGENTA" "$C_RESET" "$content" "$pad_needed" "" "$C_MAGENTA" "$C_RESET"
}

draw_header() {
    draw_box_top
    draw_row "${C_WHITE}${C_INVERSE}  ${APP_TITLE}  ${C_RESET} ${C_GREY}${APP_SUBTITLE}${C_RESET}"
}

# New Sliding Tabs (Backported from Template)
draw_tabs() {
    if (( TAB_SCROLL_START > CURRENT_TAB )); then
        TAB_SCROLL_START=$CURRENT_TAB
    fi

    local tab_line
    local -i max_tab_width=$(( BOX_INNER_WIDTH - 6 ))
    
    LEFT_ARROW_ZONE=""
    RIGHT_ARROW_ZONE=""
    TAB_ZONES=()

    while true; do
        tab_line="${C_MAGENTA}│ "
        local -i current_col=3
        local -i used_len=0
        local -i i

        if (( TAB_SCROLL_START > 0 )); then
            tab_line+="${C_YELLOW}«${C_RESET} "
            LEFT_ARROW_ZONE="$current_col:$((current_col+1))"
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        else
            tab_line+="  "
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        fi

        for (( i = TAB_SCROLL_START; i < TAB_COUNT; i++ )); do
            local name="${TABS[i]}"
            local t_len=${#name}
            local chunk_len=$(( t_len + 4 ))

            local reserve=0
            if (( i < TAB_COUNT - 1 )); then reserve=2; fi

            if (( used_len + chunk_len + reserve > max_tab_width )); then
                if (( i <= CURRENT_TAB )); then
                    TAB_SCROLL_START=$(( TAB_SCROLL_START + 1 ))
                    continue 2
                fi
                tab_line+="${C_YELLOW}» ${C_RESET}"
                RIGHT_ARROW_ZONE="$current_col:$((current_col+1))"
                used_len=$(( used_len + 2 ))
                break
            fi

            local zone_start=$current_col
            if (( i == CURRENT_TAB )); then
                tab_line+="${C_CYAN}${C_INVERSE} ${name} ${C_RESET}${C_MAGENTA}│ "
            else
                tab_line+="${C_GREY} ${name} ${C_MAGENTA}│ "
            fi
            
            TAB_ZONES+=("${zone_start}:$(( zone_start + t_len + 1 ))")
            used_len=$(( used_len + chunk_len ))
            current_col=$(( current_col + chunk_len ))
        done

        local pad=$(( BOX_INNER_WIDTH + 1 - used_len ))
        if (( pad > 0 )); then
            local pad_buf
            printf -v pad_buf '%*s' "$pad" ''
            tab_line+="$pad_buf"
        fi
        
        tab_line+="${C_MAGENTA}│${C_RESET}"
        break
    done
    
    printf '%s\n' "$tab_line"
    draw_separator
}

draw_mon_list() {
    local -i start=$SCROLL_OFFSET
    local -i end=$(( start + MAX_DISPLAY_ROWS ))
    local -i count=${#MON_LIST[@]}
    local -i drawn=0

    local -i i
    for (( i = start; i < end && i < count; i++ )); do
        local mon="${MON_LIST[$i]}"
        local state info pos line_str padded_mon

        # Truncation logic from Template
        local -i max_len=$(( ITEM_PADDING - 1 ))
        if (( ${#mon} > ITEM_PADDING )); then
            printf -v padded_mon "%-${max_len}s…" "${mon:0:max_len}"
        else
            printf -v padded_mon "%-${ITEM_PADDING}s" "$mon"
        fi

        if [[ "${MON_ENABLED["$mon"]}" == "true" ]]; then
            state="${C_GREEN}ON ${C_RESET}"
        else
            state="${C_RED}OFF${C_RESET}"
        fi

        info="${MON_CUR_RES["$mon"]}@${MON_CUR_RATE["$mon"]}Hz ${MON_SCALE["$mon"]}x"
        pos="(${MON_X["$mon"]},${MON_Y["$mon"]})"

        if (( i == SELECTED_ROW )); then
            line_str="${C_CYAN}➤ ${padded_mon}${C_RESET} [${state}] ${info} ${C_GREY}${pos}${C_RESET}"
        else
            line_str="  ${padded_mon} [${state}] ${info} ${C_GREY}${pos}${C_RESET}"
        fi
        draw_row "$line_str"
        (( drawn++ ))
    done

    local -i filler
    for (( filler = drawn; filler < MAX_DISPLAY_ROWS; filler++ )); do
        draw_row ""
    done

    draw_separator
    if (( SELECTED_ROW == count )); then
        draw_row "${C_CYAN}➤ [Save & Apply Configuration]${C_RESET}"
    else
        draw_row "  [Save & Apply Configuration]"
    fi
}

draw_edit_view() {
    local mon="$CURRENT_MON"
    local enabled="${MON_ENABLED["$mon"]}"

    draw_row "${C_YELLOW}Editing: ${mon}${C_RESET}"
    draw_separator

    local -a fields=("Enabled" "Refresh Rate" "Resolution" "Scale" "Rotation" "Bitdepth" "VRR" "---" "Anchor Mode" "Anchor Target" "X" "Y")
    local -i drawn=0
    local -i i

    for i in "${!fields[@]}"; do
        local label="${fields[$i]}"

        if [[ "$label" == "---" ]]; then
            draw_separator
            (( drawn++ ))
            continue
        fi

        local val=""
        case $i in
            0)
                if [[ "$enabled" == "true" ]]; then
                    val="${C_GREEN}True${C_RESET}"
                else
                    val="${C_RED}False${C_RESET}"
                fi
                ;;
            1) 
                local r_int
                r_int=$(float_round_int "${MON_CUR_RATE["$mon"]}")
                val="~${r_int} Hz (Bucket)" 
                ;;
            2) val="${MON_CUR_RES["$mon"]} @ ${MON_CUR_RATE["$mon"]}Hz" ;;
            3) val="${MON_SCALE["$mon"]}" ;;
            4)
                local ti=${MON_TRANSFORM["$mon"]}
                val="${TRANSFORMS[$ti]}"
                ;;
            5) val="${MON_BITDEPTH["$mon"]}-bit" ;;
            6)
                case "${MON_VRR["$mon"]}" in 0) val="Off" ;; 1) val="On" ;; 2) val="Full" ;; esac
                ;;
            8)
                local am=${UI_ANCHOR_MODE["$mon"]}
                val="${ANCHOR_POSITIONS[$am]}"
                ;;
            9)
                local at="${UI_ANCHOR_TARGET["$mon"]}"
                val="${at:-None}"
                ;;
            10)
                val="${MON_X["$mon"]}"
                if (( UI_ANCHOR_MODE["$mon"] != 0 )); then
                    val="${C_GREY}(Auto) ${val}${C_RESET}"
                fi
                ;;
            11)
                val="${MON_Y["$mon"]}"
                if (( UI_ANCHOR_MODE["$mon"] != 0 )); then
                    val="${C_GREY}(Auto) ${val}${C_RESET}"
                fi
                ;;
        esac

        local prefix="  "
        if (( i == SELECTED_ROW )); then
            prefix="${C_CYAN}➤ "
        fi
        draw_row "$(printf '%s%-14s : %s%s' "$prefix" "$label" "$val" "$C_RESET")"
        (( drawn++ ))
    done

    local -i k
    for (( k = drawn; k < MAX_DISPLAY_ROWS; k++ )); do
        draw_row ""
    done

    draw_separator
    draw_row "${C_CYAN} [Esc] Back  [Enter] Select  [h/l] Adjust  [s] Save${C_RESET}"
}

draw_picker() {
    local title=""
    if [[ "$PICKER_TYPE" == "RATE" ]]; then
        title="Select Refresh Rate Bucket (Auto-Match Res)"
    else
        title="Select Mode (Resolution @ Exact Rate)"
    fi
    
    draw_row "${C_YELLOW}${title}${C_RESET}"
    draw_separator

    local -i start=$PICKER_SCROLL
    local -i end=$(( start + MAX_DISPLAY_ROWS ))
    local -i count=${#PICKER_LIST[@]}
    local -i drawn=0

    local -i i
    for (( i = start; i < end && i < count; i++ )); do
        local item="${PICKER_LIST[$i]}"
        if (( i == PICKER_ROW )); then
            draw_row "${C_CYAN}➤ ${item}${C_RESET}"
        else
            draw_row "  ${item}"
        fi
        (( drawn++ ))
    done

    local -i f
    for (( f = drawn; f < MAX_DISPLAY_ROWS; f++ )); do
        draw_row ""
    done

    draw_separator
    draw_row "${C_CYAN} [Esc] Cancel  [Enter] Confirm${C_RESET}"
}

draw_globals() {
    local vfr_state desc_state
    if (( GLB_VFR == 1 )); then vfr_state="${C_GREEN}Enabled${C_RESET}"; else vfr_state="${C_RED}Disabled${C_RESET}"; fi
    if (( GLB_USE_DESC == 1 )); then desc_state="${C_GREEN}Description${C_RESET}"; else desc_state="${C_YELLOW}Port Name${C_RESET}"; fi

    if (( SELECTED_ROW == 0 )); then
        draw_row "${C_CYAN}➤ VFR (Variable Frame Rate)${C_RESET} : ${vfr_state}"
    else
        draw_row "  VFR (Variable Frame Rate) : ${vfr_state}"
    fi

    if (( SELECTED_ROW == 1 )); then
        draw_row "${C_CYAN}➤ Config ID Method${C_RESET}          : ${desc_state}"
    else
        draw_row "  Config ID Method          : ${desc_state}"
    fi

    local -i k
    for (( k = 2; k < MAX_DISPLAY_ROWS; k++ )); do
        draw_row ""
    done

    draw_separator
    if (( SELECTED_ROW == 2 )); then
        draw_row "${C_CYAN}➤ [Save & Apply Configuration]${C_RESET}"
    else
        draw_row "  [Save & Apply Configuration]"
    fi
}

draw_ui() {
    printf '%s' "$CURSOR_HOME"
    draw_header

    case $CURRENT_VIEW in
        0) draw_tabs; if (( CURRENT_TAB == 0 )); then draw_mon_list; else draw_globals; fi ;;
        1) draw_edit_view ;;
        2) draw_picker ;;
    esac

    draw_box_bottom
    printf '%s [Mouse/Vim] Nav  [Enter] Select  [s] Save  [q] Quit%s%s\n' "$C_GREY" "$C_RESET" "$CLR_EOS"
}

# =============================================================================
# ▼ INPUT HANDLING ▼
# =============================================================================

adjust_value() {
    local -i dir=$1
    local mon="$CURRENT_MON"

    case $SELECTED_ROW in
        0)
            if [[ "${MON_ENABLED["$mon"]}" == "true" ]]; then
                MON_ENABLED["$mon"]="false"
            else
                MON_ENABLED["$mon"]="true"
            fi
            ;;
        1)
            mapfile -t PICKER_LIST < <(get_available_rate_buckets "$mon")
            PICKER_ROW=0
            PICKER_SCROLL=0
            PICKER_TYPE="RATE"
            CURRENT_VIEW=2
            ;;
        2)
            mapfile -t PICKER_LIST < <(get_all_modes_combined "$mon")
            PICKER_ROW=0
            PICKER_SCROLL=0
            PICKER_TYPE="RES"
            CURRENT_VIEW=2
            ;;
        3)
            local current="${MON_SCALE["$mon"]}"
            local step
            step=$(awk -v d="$dir" 'BEGIN { printf "%.2f", d * 0.05 }')
            float_add "$current" "$step"
            local new_val="$REPLY"
            if float_lt "$new_val" "0.25"; then new_val="0.25"; fi
            MON_SCALE["$mon"]="$new_val"
            recalc_position "$mon"
            ;;
        4)
            local -i t=${MON_TRANSFORM["$mon"]}
            MON_TRANSFORM["$mon"]=$(( (t + dir + 8) % 8 ))
            recalc_position "$mon"
            ;;
        5)
            if [[ "${MON_BITDEPTH["$mon"]}" == "8" ]]; then
                MON_BITDEPTH["$mon"]="10"
            else
                MON_BITDEPTH["$mon"]="8"
            fi
            ;;
        6)
            local -i v=${MON_VRR["$mon"]}
            MON_VRR["$mon"]=$(( (v + dir + 3) % 3 ))
            ;;
        8)
            local -i m=${UI_ANCHOR_MODE["$mon"]}
            local -i c=${#ANCHOR_POSITIONS[@]}
            UI_ANCHOR_MODE["$mon"]=$(( (m + dir + c) % c ))
            recalc_position "$mon"
            ;;
        9)
            local ct="${UI_ANCHOR_TARGET["$mon"]}"
            local -a opts=()
            local m_iter
            for m_iter in "${MON_LIST[@]}"; do
                [[ "$m_iter" != "$mon" ]] && opts+=("$m_iter")
            done
            if (( ${#opts[@]} > 0 )); then
                local -i idx=0
                local -i ii
                for (( ii = 0; ii < ${#opts[@]}; ii++ )); do
                    [[ "${opts[$ii]}" == "$ct" ]] && idx=$ii
                done
                idx=$(( (idx + dir + ${#opts[@]}) % ${#opts[@]} ))
                UI_ANCHOR_TARGET["$mon"]="${opts[$idx]}"
                recalc_position "$mon"
            fi
            ;;
        10)
            if (( UI_ANCHOR_MODE["$mon"] == 0 )); then
                MON_X["$mon"]=$(( MON_X["$mon"] + (dir * 10) ))
            fi
            ;;
        11)
            if (( UI_ANCHOR_MODE["$mon"] == 0 )); then
                MON_Y["$mon"]=$(( MON_Y["$mon"] + (dir * 10) ))
            fi
            ;;
    esac
}

do_save_with_prompt() {
    printf '%s%s' "$CLR_SCREEN" "$CURSOR_HOME"
    save_config
    printf 'Done. Press any key.\n'
    IFS= read -rsn1 || true
}

# --- Router Logic ---

switch_tab() {
    local -i dir=${1:-1}
    CURRENT_TAB=$(( (CURRENT_TAB + dir + TAB_COUNT) % TAB_COUNT ))
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

handle_key_main() {
    local key="$1"
    local -i count=${#MON_LIST[@]}
    # +1 for "Save" button
    local -i total_items=$(( count + 1 ))
    
    # If Globals tab
    if (( CURRENT_TAB == 1 )); then
        total_items=3
    fi

    case "$key" in
        k|K|'[A') (( SELECTED_ROW > 0 )) && (( SELECTED_ROW-- )) || true ;;
        j|J|'[B') (( SELECTED_ROW < total_items - 1 )) && (( SELECTED_ROW++ )) || true ;;
        g|'[H'|'[1~')   SELECTED_ROW=0; SCROLL_OFFSET=0 ;;
        G|'[F'|'[4~')
            SELECTED_ROW=$(( total_items - 1 ))
            SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
            (( SCROLL_OFFSET < 0 )) && SCROLL_OFFSET=0
            ;;
        '[5~') # PageUp
            SELECTED_ROW=$(( SELECTED_ROW - MAX_DISPLAY_ROWS ))
            (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
            ;;
        '[6~') # PageDown
            SELECTED_ROW=$(( SELECTED_ROW + MAX_DISPLAY_ROWS ))
            (( SELECTED_ROW >= total_items )) && SELECTED_ROW=$(( total_items - 1 ))
            ;;
        $'\t'|'[Z') switch_tab 1 ;;
        s|S) do_save_with_prompt ;;
        l|L|''|$'\n')
            if (( CURRENT_TAB == 0 )); then
                if (( SELECTED_ROW == count )); then
                    do_save_with_prompt
                elif (( SELECTED_ROW < count )); then
                    CURRENT_MON="${MON_LIST[$SELECTED_ROW]}"
                    LIST_SAVED_ROW=$SELECTED_ROW
                    CURRENT_VIEW=1
                    SELECTED_ROW=0
                fi
            else
                # Globals Logic
                case $SELECTED_ROW in
                    0) GLB_VFR=$(( 1 - GLB_VFR )) ;;
                    1) GLB_USE_DESC=$(( 1 - GLB_USE_DESC )) ;;
                    2) do_save_with_prompt ;;
                esac
            fi
            ;;
        q|Q) exit 0 ;;
    esac

    # Update scroll
    if (( SELECTED_ROW < SCROLL_OFFSET )); then
        SCROLL_OFFSET=$SELECTED_ROW
    elif (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )); then
        SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
    fi
}

handle_key_edit() {
    local key="$1"
    case "$key" in
        k|K|'[A')
            (( SELECTED_ROW > 0 )) && (( SELECTED_ROW-- )) || true
            (( SELECTED_ROW == 7 )) && SELECTED_ROW=6
            ;;
        j|J|'[B')
            (( SELECTED_ROW < 11 )) && (( SELECTED_ROW++ )) || true
            (( SELECTED_ROW == 7 )) && SELECTED_ROW=8
            ;;
        h|H|'[D') adjust_value -1 ;;
        l|L|'[C') adjust_value 1 ;;
        ''|$'\n') adjust_value 1 ;;
        ESC)
            CURRENT_VIEW=0
            SELECTED_ROW=$LIST_SAVED_ROW
            ;;
        s|S) do_save_with_prompt ;;
    esac
}

handle_key_picker() {
    local key="$1"
    local -i count=${#PICKER_LIST[@]}

    case "$key" in
        k|K|'[A') (( PICKER_ROW > 0 )) && (( PICKER_ROW-- )) || true ;;
        j|J|'[B') (( PICKER_ROW < count - 1 )) && (( PICKER_ROW++ )) || true ;;
        ''|$'\n')
            local selection="${PICKER_LIST[$PICKER_ROW]}"
            if [[ -z "$selection" ]]; then
                CURRENT_VIEW=1
                return
            fi

            if [[ "$PICKER_TYPE" == "RATE" ]]; then
                local bucket="$selection"
                local bucket_resolutions
                bucket_resolutions=$(get_resolutions_for_bucket "$CURRENT_MON" "$bucket")
                local current_res="${MON_CUR_RES["$CURRENT_MON"]}"
                local best_new_res=""
                if echo "$bucket_resolutions" | grep -Fxq "$current_res"; then
                     best_new_res="$current_res"
                else
                     best_new_res=$(echo "$bucket_resolutions" | head -n1)
                fi
                if [[ -n "$best_new_res" ]]; then
                    find_exact_rate_in_bucket "$CURRENT_MON" "$best_new_res" "$bucket"
                    MON_CUR_RES["$CURRENT_MON"]="$best_new_res"
                    MON_CUR_RATE["$CURRENT_MON"]="$REPLY"
                    recalc_position "$CURRENT_MON"
                fi
            elif [[ "$PICKER_TYPE" == "RES" ]]; then
                local r_res="${selection%% @*}"
                local r_rate="${selection#*@ }"
                r_rate="${r_rate%Hz}"
                MON_CUR_RES["$CURRENT_MON"]="$r_res"
                MON_CUR_RATE["$CURRENT_MON"]="$r_rate"
                recalc_position "$CURRENT_MON"
            fi
            CURRENT_VIEW=1
            ;;
        ESC) CURRENT_VIEW=1 ;;
    esac

    if (( PICKER_ROW < PICKER_SCROLL )); then
        PICKER_SCROLL=$PICKER_ROW
    elif (( PICKER_ROW >= PICKER_SCROLL + MAX_DISPLAY_ROWS )); then
        PICKER_SCROLL=$(( PICKER_ROW - MAX_DISPLAY_ROWS + 1 ))
    fi
}

handle_mouse() {
    local seq="$1"
    local inner="${seq#*<}"
    local terminator="${seq: -1}"
    local btn col row
    IFS=';' read -r btn col row <<< "${inner%[Mm]}"

    if [[ ! "$btn" =~ ^[0-9]+$ ]]; then return 0; fi
    if [[ ! "$col" =~ ^[0-9]+$ ]]; then return 0; fi
    if [[ ! "$row" =~ ^[0-9]+$ ]]; then return 0; fi

    # Scroll Wheel (64=Up, 65=Down)
    if (( btn == 64 )); then
        if (( CURRENT_VIEW == 0 )); then handle_key_main "k"; fi
        if (( CURRENT_VIEW == 1 )); then handle_key_edit "k"; fi
        if (( CURRENT_VIEW == 2 )); then handle_key_picker "k"; fi
        return
    fi
    if (( btn == 65 )); then
        if (( CURRENT_VIEW == 0 )); then handle_key_main "j"; fi
        if (( CURRENT_VIEW == 1 )); then handle_key_edit "j"; fi
        if (( CURRENT_VIEW == 2 )); then handle_key_picker "j"; fi
        return
    fi

    if [[ "$terminator" != "m" ]]; then return; fi

    # Tab Click Logic (Template Synced)
    if (( row == TAB_ROW + 1 && CURRENT_VIEW == 0 )); then
        if [[ -n "$LEFT_ARROW_ZONE" ]]; then
            local start="${LEFT_ARROW_ZONE%%:*}"
            local end="${LEFT_ARROW_ZONE##*:}"
            if (( col >= start && col <= end )); then switch_tab -1; return 0; fi
        fi
        if [[ -n "$RIGHT_ARROW_ZONE" ]]; then
            local start="${RIGHT_ARROW_ZONE%%:*}"
            local end="${RIGHT_ARROW_ZONE##*:}"
            if (( col >= start && col <= end )); then switch_tab 1; return 0; fi
        fi

        local i
        for (( i = 0; i < TAB_COUNT; i++ )); do
            if [[ -z "${TAB_ZONES[i]:-}" ]]; then continue; fi
            local zone="${TAB_ZONES[i]}"
            local start="${zone%%:*}"
            local end="${zone##*:}"
            if (( col >= start && col <= end )); then set_tab "$(( i + TAB_SCROLL_START ))"; return 0; fi
        done
        return
    fi

    if (( row >= ITEM_START_ROW )); then
        local -i target_idx=$(( row - ITEM_START_ROW ))

        case $CURRENT_VIEW in
            0)
                # Bounds check
                if (( target_idx >= MAX_DISPLAY_ROWS )); then 
                     # Allow click on SAVE button row
                     if (( target_idx == MAX_DISPLAY_ROWS + 1 )); then
                         if (( CURRENT_TAB == 0 )); then SELECTED_ROW=${#MON_LIST[@]}; else SELECTED_ROW=2; fi
                         handle_key_main ""
                     fi
                     return
                fi
                target_idx=$(( target_idx + SCROLL_OFFSET ))
                
                local max_sel
                if (( CURRENT_TAB == 0 )); then max_sel=${#MON_LIST[@]}; else max_sel=3; fi
                
                if (( target_idx < max_sel )); then
                    SELECTED_ROW=$target_idx
                    handle_key_main ""
                fi
                ;;
            1)
                if (( target_idx == 7 )); then return; fi
                if (( target_idx <= 11 )); then
                    SELECTED_ROW=$target_idx
                    handle_key_edit "l"
                fi
                ;;
            2)
                target_idx=$(( target_idx + PICKER_SCROLL ))
                if (( target_idx < ${#PICKER_LIST[@]} )); then
                    PICKER_ROW=$target_idx
                    handle_key_picker ""
                fi
                ;;
        esac
    fi
}

handle_input_router() {
    local key="$1"
    local escape_seq=""

    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
            # Logic for Alt+Enter detection
            if [[ "$key" == "" || "$key" == $'\n' ]]; then
                key=$'\e\n'
            fi
        else
            key="ESC"
        fi
    fi

    if [[ "$key" == '['* ]]; then
        if [[ "$key" =~ '<'*[mM]$ ]]; then
            handle_mouse "$key"
            return
        fi
    fi

    case $CURRENT_VIEW in
        0) handle_key_main "$key" ;;
        1) handle_key_edit "$key" ;;
        2) handle_key_picker "$key" ;;
    esac
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

# =============================================================================
# ▼ MAIN ▼
# =============================================================================

main() {
    if (( BASH_VERSINFO[0] < 5 )); then
        printf '%s[ERROR]%s Bash 5.0+ required.\n' "$C_RED" "$C_RESET" >&2
        exit 1
    fi
    if [[ ! -t 0 ]]; then
        printf '%s[ERROR]%s TTY required.\n' "$C_RED" "$C_RESET" >&2
        exit 1
    fi

    refresh_hardware

    ORIG_STTY=$(stty -g 2>/dev/null) || ORIG_STTY=""
    stty -echo -icanon min 1 time 0 2>/dev/null

    printf '%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN"
    
    # BACKPORTED FEATURE: Redraw on resize
    trap 'draw_ui' WINCH
    
    set +e

    local key
    while true; do
        draw_ui
        IFS= read -rsn1 key || break
        handle_input_router "$key"
    done
}

main "$@"
