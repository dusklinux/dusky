#!/usr/bin/env bash
# ==============================================================================
# ARCH LINUX :: UWSM :: MATUGEN & AWWW ROFI UI
# ==============================================================================
# Description: Advanced interactive Rofi interface for theme_ctl.sh.
#              - Wizard-style state-machine navigation
#              - Fluid "Back" and "Cancel" propagation with session memory
#              - Real-time state awareness via strict native parsing
#              - Logical submenus for Wallpapers and Color Presets
#              - Frontend validation and sanitized notification feedback
#              - Safe, eval-free Theme & Animation Preset management system
#              - In-memory menu state persistence with zero-fork disk sync
#              - Zero-locking architecture compliant with Wayland/Hyprland
# ==============================================================================

if (( BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 1) )); then
    printf 'ERROR: Bash 5.1+ is required; running %s.\n' "$BASH_VERSION" >&2
    exit 1
fi

set -Eeuo pipefail
shopt -s inherit_errexit

# --- CONFIGURATION ---
readonly THEME_CTL="${HOME:?HOME is not set}/user_scripts/theme_matugen/theme_ctl.sh"
readonly SETTINGS_DIR="${HOME}/.config/dusky/settings/dusky_theme"
readonly PRESETS_FILE="${SETTINGS_DIR}/rofi_theme_presets"
readonly MEMORY_FILE="${SETTINGS_DIR}/rofi_theme_memory"
readonly APP_NAME="theme-ui"
readonly ROFI_THEME_STR='window { width: 500px; } listview { lines: 12; }'

readonly -a REQUIRED_CMDS=(rofi mktemp mv rm)

# --- MENU DATA ARRAYS ---
readonly -a OPTS_MODE=(dark light)
readonly -a OPTS_SCHEME=(scheme-tonal-spot scheme-vibrant scheme-fruit-salad scheme-expressive scheme-fidelity scheme-rainbow scheme-neutral scheme-monochrome scheme-content scheme-smart disable)
readonly -a OPTS_CONTRAST=(0 -0.8 -0.6 -0.4 -0.2 0.2 0.4 0.6 0.8 1.0 disable)
readonly -a OPTS_INDEX=(0 1 2 3)
readonly -a OPTS_BASE16=(disable wal)

readonly -a OPTS_TRANS_TYPE=(random simple fade left right top bottom wipe wave grow center any outer none disable)
readonly -a OPTS_TRANS_DUR=(disable 0.5 1 2 3 5 10)
readonly -a OPTS_TRANS_FPS=(disable 30 60 90 120 144)
readonly -a OPTS_TRANS_BEZ=(disable ".54,0,.34,.99" "0,0,1,1" ".85,0,.15,1" ".17,.67,.83,.67")
readonly -a OPTS_TRANS_ANG=(disable 0 30 45 90 135 180 225 270 315)
readonly -a OPTS_TRANS_POS=(disable center top left right bottom top-left top-right bottom-left bottom-right)

# 16 Comprehensive Color Presets
readonly -A OPTS_COLORS=(
    ["Red"]="#FF0000"
    ["Blue"]="#0000FF"
    ["Yellow"]="#FFFF00"
    ["Green"]="#00FF00"
    ["Cyan"]="#00FFFF"
    ["Purple"]="#800080"
    ["Orange"]="#FFA500"
    ["Pink"]="#FFC0CB"
    ["Brown"]="#A52A2A"
    ["Golden"]="#FFD700"
    ["Light Green"]="#90EE90"
    ["Bright Yellow"]="#FFFFE0"
    ["Bright Green"]="#66FF00"
    ["Sky Blue"]="#87CEEB"
    ["White"]="#FFFFFF"
    ["Black"]="#000000"
)

readonly -a OPTS_COLOR_KEYS=(
    "Red" "Blue" "Yellow" "Green" "Cyan" "Purple"
    "Orange" "Pink" "Brown" "Golden" "Light Green"
    "Bright Yellow" "Bright Green" "Sky Blue" "White" "Black"
)

# --- GLOBAL STATE VARIABLES ---
CUR_MODE="dark"; CUR_TYPE="scheme-tonal-spot"; CUR_CONTRAST="0"; CUR_INDEX="0"; CUR_BASE16="disable"
CUR_T_TYPE="random"; CUR_T_DUR="2"; CUR_T_FPS="60"; CUR_T_BEZ=".54,0,.34,.99"; CUR_T_ANG="30"; CUR_T_POS="center"

declare -A MENU_MEMORY=()
declare -i MEMORY_LOADED=0
_TEMP_FILE=""

# --- ERROR HANDLING, CLEANUP & LOGGING ---
have_cmd() { command -v "$1" >/dev/null 2>&1; }
log_info() { have_cmd logger && logger -p user.info -t "$APP_NAME" -- <<< "$1" || true; }
log_error() { have_cmd logger && logger -p user.err -t "$APP_NAME" -- <<< "$1" || true; }

# Escape Pango XML entities and strip ANSI escape sequences so mako/swaync/dunst never fail
sanitize_notify_str() {
    local s="${1:-}"
    local esc=$'\x1b'
    while [[ "$s" =~ (${esc}\[[0-9;]*[a-zA-Z]) ]]; do
        s="${s//"${BASH_REMATCH[1]}"/}"
    done
    s="${s//&/\&amp;}"
    s="${s//</\&lt;}"
    s="${s//>/\&gt;}"
    printf '%s' "$s"
}

notify() {
    if have_cmd notify-send; then
        local safe_title safe_body
        safe_title=$(sanitize_notify_str "$2")
        safe_body=$(sanitize_notify_str "$3")
        notify-send -u "$1" -- "$safe_title" "$safe_body" >/dev/null 2>&1 || true
    fi
}

cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "${_TEMP_FILE:-}" && -e "$_TEMP_FILE" ]]; then
        rm -f -- "$_TEMP_FILE" 2>/dev/null || true
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# Extract the primary ERROR message, collapse multiline diagnostics, and truncate
format_error_msg() {
    local raw="${1:-}"
    local fallback="${2:-Check system logs for details.}"
    local line matched="" capturing=0

    while IFS= read -r line || [[ -n "$line" ]]; do
        if (( ! capturing )) && [[ "$line" == ERROR:* ]]; then
            matched="${line#ERROR: }"
            capturing=1
        elif (( capturing )); then
            [[ -n "${line//[[:space:]]/}" ]] && matched+=" ${line#"${line%%[![:space:]]*}"}"
        fi
    done <<< "$raw"

    if [[ -z "$matched" ]]; then
        matched="${raw//$'\n'/ }"
        matched="${matched#"${matched%%[![:space:]]*}"}"
    fi

    matched="${matched:-$fallback}"
    if (( ${#matched} > 220 )); then
        matched="${matched:0:217}..."
    fi
    printf '%s' "$matched"
}

invoke_theme_ctl() {
    local -n _out_ref="$1"
    shift
    local status=0
    _out_ref=$("$THEME_CTL" "$@" 2>&1) || status=$?
    # Successful backend exits can still contain hook warnings. Keep those
    # diagnostics in the journal; get's output is configuration, not a log.
    if (( status == 0 )) && [[ "${1:-}" != "get" && -n "$_out_ref" ]]; then
        log_info "$_out_ref"
    fi
    return "$status"
}

fatal() {
    log_error "$1"
    notify critical "Theme UI Error" "${2:-$1}"
    exit 1
}

on_unexpected_error() {
    local exit_code=$1
    local line_no=$2
    if (( exit_code == 129 || exit_code == 130 || exit_code == 143 )); then
        exit "$exit_code"
    fi
    log_error "Unhandled error at line ${line_no} (exit ${exit_code})."
    notify critical "Theme UI Error" "Unexpected failure at line ${line_no}. Check journalctl."
    exit "$exit_code"
}
trap 'on_unexpected_error $? $LINENO' ERR

require_commands() {
    local cmd
    for cmd in "${REQUIRED_CMDS[@]}"; do
        have_cmd "$cmd" || fatal "Missing required command: $cmd" "Missing dependency: $cmd"
    done
    [[ -f "$THEME_CTL" && -x "$THEME_CTL" ]] || fatal "Controller script missing/non-executable: $THEME_CTL"
}

# --- ATOMIC FILE & IN-MEMORY STATE MANAGEMENT ---
ensure_settings_dir() {
    [[ -d "$SETTINGS_DIR" ]] || mkdir -p -- "$SETTINGS_DIR" ||
        fatal "Could not create settings directory: $SETTINGS_DIR"
}

ensure_presets_file() {
    ensure_settings_dir
    [[ -f "$PRESETS_FILE" ]] || : > "$PRESETS_FILE"
}

load_memory_cache() {
    (( ! MEMORY_LOADED )) || return 0
    MEMORY_LOADED=1
    [[ -f "$MEMORY_FILE" && -r "$MEMORY_FILE" ]] || return 0

    local key val
    while IFS='=' read -r key val || [[ -n "$key" ]]; do
        [[ -n "$key" ]] && MENU_MEMORY["$key"]="$val"
    done < "$MEMORY_FILE"
    return 0
}

read_memory() {
    local menu_id="$1"
    load_memory_cache
    printf '%s' "${MENU_MEMORY[$menu_id]:-}"
}

write_memory() {
    local menu_id="$1"
    local value="$2"
    local key

    load_memory_cache
    if [[ "${MENU_MEMORY[$menu_id]:-}" == "$value" ]]; then
        return 0
    fi
    MENU_MEMORY["$menu_id"]="$value"

    ensure_settings_dir
    _TEMP_FILE=$(mktemp "${SETTINGS_DIR}/rofi_theme_memory.XXXXXX") ||
        fatal "Could not create menu-memory temporary file"

    for key in "${!MENU_MEMORY[@]}"; do
        printf '%s=%s\n' "$key" "${MENU_MEMORY[$key]}" ||
            fatal "Could not write menu memory"
    done > "$_TEMP_FILE" || fatal "Could not open menu-memory temporary file"

    mv -fT -- "$_TEMP_FILE" "$MEMORY_FILE" ||
        fatal "Could not replace menu memory: $MEMORY_FILE"
    _TEMP_FILE=""
}

# --- ROFI WRAPPERS ---
is_rofi_abort_exit() {
    local exit_code=$1
    [[ $exit_code -eq 1 || $exit_code -eq 130 || $exit_code -eq 143 ]] && return 0
    (( exit_code >= 10 && exit_code <= 28 ))
}

# Return selections in the caller so fatal menu errors exit the UI itself.
# Usage: run_menu OUTPUT_VARIABLE PROMPT ALLOW_CUSTOM DEFAULT [OPTIONS...]
run_menu() {
    local -n _menu_result="$1"
    shift
    local prompt="$1"
    local allow_custom="$2"
    local default_selection="${3:-}"
    shift 3
    local -a options=("$@")
    local selected="" exit_code=0 i

    local -a rofi_cmd=(rofi -dmenu -i -p "$prompt" -theme-str "$ROFI_THEME_STR" -format s)
    [[ "$allow_custom" == "false" ]] && rofi_cmd+=("-no-custom")

    if (( ${#options[@]} > 0 )); then
        local -i selected_row=0
        local -i found_match=0
        if [[ -n "$default_selection" ]]; then
            for i in "${!options[@]}"; do
                if [[ "${options[$i]}" == "$default_selection" ]]; then
                    selected_row=$i
                    found_match=1
                    break
                fi
            done

            # If custom input is allowed and the active value is not in the preset list,
            # insert it at row 1 (right below Back/Cancel) so it stays visible and selected.
            if (( ! found_match )) && [[ "$allow_custom" == "true" ]]; then
                options=("${options[0]}" "$default_selection" "${options[@]:1}")
                selected_row=1
            fi
        fi

        rofi_cmd+=("-selected-row" "$selected_row")
        selected=$(printf '%s\n' "${options[@]}" | "${rofi_cmd[@]}") || exit_code=$?
    else
        selected=$("${rofi_cmd[@]}" </dev/null) || exit_code=$?
    fi

    if (( exit_code == 0 )); then
        _menu_result="$selected"
        return 0
    fi

    if is_rofi_abort_exit "$exit_code"; then
        return 1
    fi
    fatal "Rofi failed at '$prompt' with exit code $exit_code"
}

# --- STATE SYNC ---
get_current_state() {
    local line key val state_out=""

    # Use the controller's validated, normalized state. Reading state.conf
    # directly can expose invalid values or retain stale values for missing keys.
    if ! invoke_theme_ctl state_out get; then
        fatal "Failed to query state from theme_ctl: $state_out" \
            "$(format_error_msg "$state_out" "Could not read theme settings.")"
    fi

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" == *=* ]] || continue
        key="${line%%=*}"
        val="${line#*=}"
        key="${key#"${key%%[![:space:]]*}"}"
        key="${key%"${key##*[![:space:]]}"}"
        [[ -z "$key" || "$key" == \#* ]] && continue

        val="${val#"${val%%[![:space:]]*}"}"
        val="${val%"${val##*[![:space:]]}"}"
        if [[ ${#val} -ge 2 ]]; then
            if [[ "${val:0:1}" == '"' && "${val: -1}" == '"' ]] ||
               [[ "${val:0:1}" == "'" && "${val: -1}" == "'" ]]; then
                val="${val:1:-1}"
            fi
        fi

        case "$key" in
            THEME_MODE)          CUR_MODE="$val" ;;
            MATUGEN_TYPE)        CUR_TYPE="$val" ;;
            MATUGEN_CONTRAST)    CUR_CONTRAST="$val" ;;
            SOURCE_COLOR_INDEX)  CUR_INDEX="$val" ;;
            BASE16_BACKEND)      CUR_BASE16="$val" ;;
            AWWW_TRANS_TYPE)     CUR_T_TYPE="$val" ;;
            AWWW_TRANS_DURATION) CUR_T_DUR="$val" ;;
            AWWW_TRANS_FPS)      CUR_T_FPS="$val" ;;
            AWWW_TRANS_BEZIER)   CUR_T_BEZ="$val" ;;
            AWWW_TRANS_ANGLE)    CUR_T_ANG="$val" ;;
            AWWW_TRANS_POS)      CUR_T_POS="$val" ;;
        esac
    done <<< "$state_out"
}

# --- WIZARDS (STATE MACHINES) ---

wizard_theme() {
    local state="mode"
    local -A cfg=()
    local choice

    while true; do
        case "$state" in
            "mode")
                run_menu choice "Mode" false "${cfg[mode]:-$CUR_MODE}" "  Cancel" "${OPTS_MODE[@]}" || return 0
                if [[ "$choice" == "  Cancel" ]]; then return 0; fi
                cfg[mode]="$choice"
                state="type"
                ;;
            "type")
                run_menu choice "Matugen Scheme" false "${cfg[type]:-$CUR_TYPE}" "  Back" "${OPTS_SCHEME[@]}" || return 0
                if [[ "$choice" == "  Back" ]]; then state="mode"; continue; fi
                cfg[type]="$choice"
                state="contrast"
                ;;
            "contrast")
                run_menu choice "Contrast" true "${cfg[contrast]:-$CUR_CONTRAST}" "  Back" "${OPTS_CONTRAST[@]}" || return 0
                if [[ "$choice" == "  Back" ]]; then state="type"; continue; fi
                choice="${choice#"${choice%%[![:space:]]*}"}"
                choice="${choice%"${choice##*[![:space:]]}"}"
                if [[ -z "$choice" ]]; then
                    notify critical "Invalid Contrast" "Contrast value cannot be empty."
                    continue
                fi
                cfg[contrast]="$choice"
                state="index"
                ;;
            "index")
                run_menu choice "Color Index" false "${cfg[index]:-$CUR_INDEX}" "  Back" "${OPTS_INDEX[@]}" || return 0
                if [[ "$choice" == "  Back" ]]; then state="contrast"; continue; fi
                cfg[index]="$choice"
                state="base16"
                ;;
            "base16")
                run_menu choice "Base16 Backend" false "${cfg[base16]:-$CUR_BASE16}" "  Back" "${OPTS_BASE16[@]}" || return 0
                if [[ "$choice" == "  Back" ]]; then state="index"; continue; fi
                cfg[base16]="$choice"
                state="apply"
                ;;
            "apply")
                notify normal "Applying Theme" "Mode: ${cfg[mode]} | Type: ${cfg[type]}"
                local err_out=""
                if ! invoke_theme_ctl err_out set --no-wall \
                    --mode "${cfg[mode]}" \
                    --type "${cfg[type]}" \
                    --contrast "${cfg[contrast]}" \
                    --index "${cfg[index]}" \
                    --base16 "${cfg[base16]}"; then
                    notify critical "Theme Failed" "$(format_error_msg "$err_out")"
                    log_error "Theme Backend Failed: ${err_out}"
                    state="mode"
                    continue
                fi
                return 0
                ;;
        esac
    done
}

wizard_animation() {
    local state="type"
    local -A cfg=()
    local choice

    while true; do
        case "$state" in
            "type")
                run_menu choice "Trans Type" false "${cfg[type]:-$CUR_T_TYPE}" "  Cancel" "${OPTS_TRANS_TYPE[@]}" || return 0
                if [[ "$choice" == "  Cancel" ]]; then return 0; fi
                cfg[type]="$choice"
                state="duration"
                ;;
            "duration")
                run_menu choice "Duration (sec)" true "${cfg[duration]:-$CUR_T_DUR}" "  Back" "${OPTS_TRANS_DUR[@]}" || return 0
                if [[ "$choice" == "  Back" ]]; then state="type"; continue; fi
                choice="${choice#"${choice%%[![:space:]]*}"}"
                choice="${choice%"${choice##*[![:space:]]}"}"
                if [[ -z "$choice" ]]; then continue; fi
                cfg[duration]="$choice"
                state="fps"
                ;;
            "fps")
                run_menu choice "FPS" true "${cfg[fps]:-$CUR_T_FPS}" "  Back" "${OPTS_TRANS_FPS[@]}" || return 0
                if [[ "$choice" == "  Back" ]]; then state="duration"; continue; fi
                choice="${choice#"${choice%%[![:space:]]*}"}"
                choice="${choice%"${choice##*[![:space:]]}"}"
                if [[ -z "$choice" ]]; then continue; fi
                cfg[fps]="$choice"
                state="bezier"
                ;;
            "bezier")
                run_menu choice "Bezier Curve" true "${cfg[bezier]:-$CUR_T_BEZ}" "  Back" "${OPTS_TRANS_BEZ[@]}" || return 0
                if [[ "$choice" == "  Back" ]]; then state="fps"; continue; fi
                choice="${choice#"${choice%%[![:space:]]*}"}"
                choice="${choice%"${choice##*[![:space:]]}"}"
                if [[ -z "$choice" ]]; then continue; fi
                cfg[bezier]="$choice"
                state="angle"
                ;;
            "angle")
                run_menu choice "Angle (Deg)" true "${cfg[angle]:-$CUR_T_ANG}" "  Back" "${OPTS_TRANS_ANG[@]}" || return 0
                if [[ "$choice" == "  Back" ]]; then state="bezier"; continue; fi
                choice="${choice#"${choice%%[![:space:]]*}"}"
                choice="${choice%"${choice##*[![:space:]]}"}"
                if [[ -z "$choice" ]]; then continue; fi
                cfg[angle]="$choice"
                state="position"
                ;;
            "position")
                run_menu choice "Position" true "${cfg[pos]:-$CUR_T_POS}" "  Back" "${OPTS_TRANS_POS[@]}" || return 0
                if [[ "$choice" == "  Back" ]]; then state="angle"; continue; fi
                choice="${choice#"${choice%%[![:space:]]*}"}"
                choice="${choice%"${choice##*[![:space:]]}"}"
                if [[ -z "$choice" ]]; then continue; fi
                cfg[pos]="$choice"
                state="apply"
                ;;
            "apply")
                notify normal "Applying Animation" "Type: ${cfg[type]} | Dur: ${cfg[duration]}"
                local err_out=""
                if ! invoke_theme_ctl err_out set --no-wall --no-regen \
                    --trans-type "${cfg[type]}" \
                    --trans-duration "${cfg[duration]}" \
                    --trans-fps "${cfg[fps]}" \
                    --trans-bezier "${cfg[bezier]}" \
                    --trans-angle "${cfg[angle]}" \
                    --trans-pos "${cfg[pos]}"; then
                    notify critical "Animation Failed" "$(format_error_msg "$err_out")"
                    log_error "Animation Backend Failed: ${err_out}"
                    state="type"
                    continue
                fi
                return 0
                ;;
        esac
    done
}

# --- PRESET MANAGEMENT ENGINE ---

# Safe, eval-free tokenizer with strict option-pair validation
deserialize_preset_args() {
    local raw_args="$1"
    local -n _out_args_ref="$2"
    local -a raw_tokens=()
    local -i i=0
    local flag val token_var

    _out_args_ref=()
    read -r -a raw_tokens <<< "$raw_args"

    (( ${#raw_tokens[@]} > 0 && ${#raw_tokens[@]} % 2 == 0 )) || return 1

    while (( i < ${#raw_tokens[@]} )); do
        flag="${raw_tokens[$i]//\\/}"
        val="${raw_tokens[$((i + 1))]//\\/}"

        for token_var in flag val; do
            local -n ref="$token_var"
            if [[ ${#ref} -ge 2 ]]; then
                if [[ "${ref:0:1}" == '"' && "${ref: -1}" == '"' ]] ||
                   [[ "${ref:0:1}" == "'" && "${ref: -1}" == "'" ]]; then
                    ref="${ref:1:-1}"
                fi
            fi
        done

        case "$flag" in
            --mode|--type|--contrast|--index|--base16|\
            --trans-type|--trans-duration|--trans-fps|\
            --trans-bezier|--trans-angle|--trans-pos)
                [[ -n "$val" ]] || return 1
                _out_args_ref+=("$flag" "$val")
                ;;
            *)
                return 1
                ;;
        esac

        (( i += 2 ))
    done
}

save_preset() {
    ensure_presets_file
    local preset_name existing_name _rest
    run_menu preset_name "Enter Preset Name [ESC to Cancel]" true "" || return 0

    preset_name="${preset_name#"${preset_name%%[![:space:]]*}"}"
    preset_name="${preset_name%"${preset_name##*[![:space:]]}"}"

    if [[ -z "$preset_name" || "$preset_name" == "  Back"* || "$preset_name" == "  Cancel"* ]]; then
        notify critical "Invalid Name" "Preset name cannot be empty or reserved."
        return 0
    fi

    if [[ "$preset_name" == *"|"* ]]; then
        notify critical "Invalid Character" "Preset name cannot contain '|'."
        return 0
    fi

    while IFS='|' read -r existing_name _rest || [[ -n "$existing_name" ]]; do
        if [[ "$existing_name" == "$preset_name" ]]; then
            notify critical "Duplicate Preset" "A preset with that name already exists."
            return 0
        fi
    done < "$PRESETS_FILE"

    get_current_state

    local -a cmd_args=(
        --mode "$CUR_MODE"
        --type "$CUR_TYPE"
        --contrast "$CUR_CONTRAST"
        --index "$CUR_INDEX"
        --base16 "$CUR_BASE16"
        --trans-type "$CUR_T_TYPE"
        --trans-duration "$CUR_T_DUR"
        --trans-fps "$CUR_T_FPS"
        --trans-bezier "${CUR_T_BEZ//[[:space:]]/}"
        --trans-angle "$CUR_T_ANG"
        --trans-pos "${CUR_T_POS//[[:space:]]/}"
    )

    local args_serialized="${cmd_args[*]}"

    _TEMP_FILE=$(mktemp "${SETTINGS_DIR}/rofi_theme_presets.XXXXXX")
    if [[ -s "$PRESETS_FILE" ]]; then
        while IFS='|' read -r existing_name _rest || [[ -n "$existing_name" ]]; do
            [[ -n "$existing_name" ]] && printf '%s|%s\n' "$existing_name" "$_rest"
        done < "$PRESETS_FILE" > "$_TEMP_FILE"
    fi
    printf '%s|%s\n' "$preset_name" "$args_serialized" >> "$_TEMP_FILE"

    mv -fT -- "$_TEMP_FILE" "$PRESETS_FILE"
    _TEMP_FILE=""

    notify normal "Preset Saved" "Successfully saved preset: $preset_name"
}

load_preset() {
    ensure_presets_file

    local -a preset_names=()
    local name rest
    while IFS='|' read -r name rest || [[ -n "$name" ]]; do
        [[ -n "$name" ]] && preset_names+=("$name")
    done < "$PRESETS_FILE"

    if (( ${#preset_names[@]} == 0 )); then
        notify normal "No Presets" "No saved presets found."
        return 0
    fi

    local -a opts=("  Back" "${preset_names[@]}")
    local choice
    run_menu choice "Load Preset" false "" "${opts[@]}" || return 0
    [[ "$choice" == "  Back" ]] && return 0

    local args=""
    while IFS='|' read -r name rest || [[ -n "$name" ]]; do
        if [[ "$name" == "$choice" ]]; then
            args="$rest"
            break
        fi
    done < "$PRESETS_FILE"

    if [[ -n "$args" ]]; then
        notify normal "Loading Preset" "Applying: $choice"

        local -a parsed_args=()
        if ! deserialize_preset_args "$args" parsed_args; then
            notify critical "Preset Error" "Preset configuration is invalid or corrupted."
            return 0
        fi

        local err_out=""
        if ! invoke_theme_ctl err_out set --no-wall "${parsed_args[@]}"; then
            notify critical "Preset Failed" "$(format_error_msg "$err_out" "Failed to apply preset: $choice")"
            log_error "Failed to apply preset: $choice with args: $args; error: $err_out"
        fi
    else
        notify critical "Error" "Could not find configurations for preset: $choice"
    fi
}

delete_preset() {
    ensure_presets_file

    local -a preset_names=()
    local name rest
    while IFS='|' read -r name rest || [[ -n "$name" ]]; do
        [[ -n "$name" ]] && preset_names+=("$name")
    done < "$PRESETS_FILE"

    if (( ${#preset_names[@]} == 0 )); then
        notify normal "No Presets" "No saved presets found to delete."
        return 0
    fi

    local -a opts=("  Back" "${preset_names[@]}")
    local choice
    run_menu choice "Delete Preset" false "" "${opts[@]}" || return 0
    [[ "$choice" == "  Back" ]] && return 0

    _TEMP_FILE=$(mktemp "${SETTINGS_DIR}/rofi_theme_presets.XXXXXX")
    while IFS='|' read -r name rest || [[ -n "$name" ]]; do
        if [[ -n "$name" && "$name" != "$choice" ]]; then
            printf '%s|%s\n' "$name" "$rest"
        fi
    done < "$PRESETS_FILE" > "$_TEMP_FILE"

    mv -fT -- "$_TEMP_FILE" "$PRESETS_FILE"
    _TEMP_FILE=""

    notify normal "Preset Deleted" "Removed preset: $choice"
}

submenu_presets() {
    local choice last_preset
    local -a opts=(
        "  Back to Main"
        "  Save Current State as Preset"
        "  Load Preset"
        "  Delete Preset"
    )

    while true; do
        last_preset=$(read_memory "presets_menu")
        run_menu choice "Theme Presets" false "$last_preset" "${opts[@]}" || return 0

        [[ -n "$choice" && "$choice" != "  Back"* ]] && write_memory "presets_menu" "$choice"

        case "$choice" in
            "  Back"*) return 0 ;;
            "  Save"*) save_preset ;;
            "  Load"*) load_preset ;;
            "  Delete"*) delete_preset ;;
        esac
    done
}

# --- SUBMENUS ---

submenu_regen() {
    local action="$1"
    local choice
    local -a opts=(
        "  Back"
        "  Yes (Regenerate Colors)"
        "󰹹  No (Just Change Wallpaper)"
    )

    run_menu choice "Regenerate Colors?" false "  Yes (Regenerate Colors)" "${opts[@]}" || return 1

    case "$choice" in
        "  Back"*) return 1 ;;
        "  "*)
            notify normal "Wallpaper & Theme" "Applying $action and extracting colors..."
            local err_out=""
            if ! invoke_theme_ctl err_out "$action"; then
                notify critical "Failed" "$(format_error_msg "$err_out" "Could not apply $action.")"
                log_error "Failed: $action; error: $err_out"
            fi
            return 0
            ;;
        "󰹹  "*)
            notify normal "Wallpaper Only" "Applying $action without regeneration..."
            local err_out=""
            if ! invoke_theme_ctl err_out "$action" --no-regen; then
                notify critical "Failed" "$(format_error_msg "$err_out" "Could not apply $action.")"
                log_error "Failed: $action --no-regen; error: $err_out"
            fi
            return 0
            ;;
    esac
}

submenu_solid_color() {
    local choice hex last_color
    local -a opts=(
        "  Back"
        "  Custom Hex Code..."
    )

    local k
    for k in "${OPTS_COLOR_KEYS[@]}"; do
        opts+=("$k (${OPTS_COLORS[$k]})")
    done

    while true; do
        last_color=$(read_memory "color_menu")
        run_menu choice "Select Solid Color" false "$last_color" "${opts[@]}" || return 1

        [[ -n "$choice" && "$choice" != "  Back"* ]] && write_memory "color_menu" "$choice"

        case "$choice" in
            "  Back"*) return 1 ;;
            "  Custom"*)
                run_menu hex "Enter Hex (e.g. FF0000) [ESC to Cancel]" true "" || continue
                hex="${hex#"${hex%%[![:space:]]*}"}"
                hex="${hex%"${hex##*[![:space:]]}"}"

                if [[ -n "$hex" ]]; then
                    if [[ ! "$hex" =~ ^#?[a-fA-F0-9]{6}$ ]]; then
                        notify critical "Invalid Hex" "The input '$hex' is not a valid hex color."
                        continue
                    fi

                    notify normal "Applying Solid Color" "Hex: $hex"
                    local err_out=""
                    if ! invoke_theme_ctl err_out color "$hex"; then
                        notify critical "Failed" "$(format_error_msg "$err_out" "Could not apply color.")"
                        log_error "Failed to apply color: $err_out"
                    fi
                    return 0
                fi
                ;;
            *)
                if [[ "$choice" =~ \((#[A-Fa-f0-9]{6})\) ]]; then
                    hex="${BASH_REMATCH[1]}"
                    notify normal "Applying Preset" "Color: $hex"
                    local err_out=""
                    if ! invoke_theme_ctl err_out color "$hex"; then
                        notify critical "Failed" "$(format_error_msg "$err_out" "Could not apply preset.")"
                        log_error "Failed to apply preset color: $err_out"
                    fi
                    return 0
                fi
                ;;
        esac
    done
}

submenu_wallpapers() {
    local choice last_wall
    local -a opts=(
        "  Back to Main"
        "  Next Wallpaper"
        "  Prev Wallpaper"
        "  Random Wallpaper"
        "  Apply Solid Color"
    )

    while true; do
        last_wall=$(read_memory "wallpaper_menu")
        run_menu choice "Wallpaper Controls" false "$last_wall" "${opts[@]}" || return 0

        [[ -n "$choice" && "$choice" != "  Back"* ]] && write_memory "wallpaper_menu" "$choice"

        case "$choice" in
            "  Back"*) return 0 ;;
            "  "*) submenu_regen "next" && return 0 ;;
            "  "*) submenu_regen "prev" && return 0 ;;
            "  "*) submenu_regen "random" && return 0 ;;
            "  "*) submenu_solid_color && return 0 ;;
        esac
    done
}

# --- MAIN LOOP ---
main() {
    require_commands

    local choice last_main
    local -a main_opts=(
        "  Theme Config Wizard (Matugen)"
        "󰹹  Animation Config Wizard (awww)"
        "  Theme & Animation Presets"
        "  Wallpaper & Color Controls"
        "  Refresh Current Colors"
        "  Reset Theme to Defaults"
        "  Exit"
    )

    while true; do
        get_current_state
        last_main=$(read_memory "main_menu")

        run_menu choice "Dusky Theme Manager" false "$last_main" "${main_opts[@]}" || exit 0

        [[ -n "$choice" && "$choice" != "  Exit" ]] && write_memory "main_menu" "$choice"

        case "$choice" in
            "  Theme"*) wizard_theme ;;
            "󰹹  Animation"*) wizard_animation ;;
            "  Theme & Animation"*) submenu_presets ;;
            "  Wallpaper"*) submenu_wallpapers ;;
            "  Refresh"*)
                notify normal "Refreshing..." "Regenerating the selected color or current wallpaper."
                local err_out=""
                if ! invoke_theme_ctl err_out refresh; then
                    notify critical "Refresh Failed" "$(format_error_msg "$err_out" "Check system logs.")"
                    log_error "Refresh failed: $err_out"
                fi
                ;;
            "  Reset"*)
                notify normal "Resetting Theme..." "Applying default configuration."
                local err_out=""
                if ! invoke_theme_ctl err_out set --defaults; then
                    notify critical "Reset Failed" "$(format_error_msg "$err_out" "Check system logs.")"
                    log_error "Reset failed: $err_out"
                fi
                ;;
            "  Exit"*) exit 0 ;;
        esac
    done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
