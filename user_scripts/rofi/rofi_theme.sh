#!/usr/bin/env bash
# ==============================================================================
# ARCH LINUX :: UWSM :: MATUGEN & AWWW ROFI UI
# ==============================================================================
# Description: Advanced interactive Rofi interface for theme_ctl.sh.
#              - Wizard-style state-machine navigation
#              - Fluid "Back" and "Cancel" propagation
#              - Real-time state awareness via strict secure parsing
#              - Logical submenus for Wallpapers and Color Presets
# ==============================================================================

set -Eeuo pipefail
shopt -s inherit_errexit

# --- CONFIGURATION ---
readonly THEME_CTL="${HOME}/user_scripts/theme_matugen/theme_ctl.sh"
readonly APP_NAME="theme-ui"
readonly ROFI_THEME_STR='window { width: 500px; } listview { lines: 12; }'

readonly -a REQUIRED_CMDS=(uwsm-app rofi)

# --- MENU DATA ARRAYS ---
readonly -a OPTS_MODE=(dark light)
readonly -a OPTS_SCHEME=(scheme-tonal-spot scheme-vibrant scheme-fruit-salad scheme-expressive scheme-fidelity scheme-rainbow scheme-neutral scheme-monochrome scheme-content disable)
readonly -a OPTS_CONTRAST=(0 -0.8 -0.6 -0.4 -0.2 0.2 0.4 0.6 0.8 1.0 disable)
readonly -a OPTS_INDEX=(0 1 2 3)
readonly -a OPTS_BASE16=(disable wal)

readonly -a OPTS_TRANS_TYPE=(random simple fade left right top bottom wipe wave grow center any outer none disable)
readonly -a OPTS_TRANS_DUR=(disable 0.5 1 2 3 5 10)
readonly -a OPTS_TRANS_FPS=(disable 30 60 90 120 144)
readonly -a OPTS_TRANS_BEZ=(disable ".54,0,.34,.99" "0,0,1,1" ".85,0,.15,1" ".17,.67,.83,.67")
readonly -a OPTS_TRANS_ANG=(disable 0 45 90 135 180 225 270 315)
readonly -a OPTS_TRANS_POS=(disable center top left right bottom top-left top-right bottom-left bottom-right)

# 16 Comprehensive Color Presets
readonly -A OPTS_COLORS=(
    ["🔴 Red"]="#FF0000"
    ["🔵 Blue"]="#0000FF"
    ["🟡 Yellow"]="#FFFF00"
    ["🟢 Green"]="#00FF00"
    ["🌐 Cyan"]="#00FFFF"
    ["🟣 Purple"]="#800080"
    ["🟠 Orange"]="#FFA500"
    ["🌸 Pink"]="#FFC0CB"
    ["🟤 Brown"]="#A52A2A"
    ["🪙 Golden"]="#FFD700"
    ["🍃 Light Green"]="#90EE90"
    ["☀️ Bright Yellow"]="#FFFFE0"
    ["🔋 Bright Green"]="#66FF00"
    ["🌌 Sky Blue"]="#87CEEB"
    ["⚪ White"]="#FFFFFF"
    ["⚫ Black"]="#000000"
)

# Keys array to maintain logical order in Rofi (Associative arrays are unordered by default)
readonly -a OPTS_COLOR_KEYS=(
    "🔴 Red" "🔵 Blue" "🟡 Yellow" "🟢 Green" "🌐 Cyan" "🟣 Purple"
    "🟠 Orange" "🌸 Pink" "🟤 Brown" "🪙 Golden" "🍃 Light Green"
    "☀️ Bright Yellow" "🔋 Bright Green" "🌌 Sky Blue" "⚪ White" "⚫ Black"
)

# --- GLOBAL STATE VARIABLES ---
CUR_MODE="dark"; CUR_TYPE="scheme-tonal-spot"; CUR_CONTRAST="0"; CUR_INDEX="0"; CUR_BASE16="disable"
CUR_T_TYPE="random"; CUR_T_DUR="2"; CUR_T_FPS="60"; CUR_T_BEZ=".54,0,.34,.99"; CUR_T_ANG="30"; CUR_T_POS="center"

# --- ERROR HANDLING & LOGGING ---
have_cmd() { command -v "$1" >/dev/null 2>&1; }
log_info() { have_cmd logger && logger -p user.info -t "$APP_NAME" -- "$1" || true; }
log_error() { have_cmd logger && logger -p user.err -t "$APP_NAME" -- "$1" || true; }
notify() { have_cmd notify-send && notify-send -u "$1" -- "$2" "$3" >/dev/null 2>&1 || true; }

fatal() {
    log_error "$1"
    notify critical "Theme UI Error" "${2:-$1}"
    exit 1
}

on_unexpected_error() {
    local exit_code=$1
    local line_no=$2
    log_error "Unhandled error at line ${line_no} (exit ${exit_code})."
    notify critical "Theme UI Error" "Unexpected failure. Check journalctl."
    exit "$exit_code"
}
trap 'on_unexpected_error $? $LINENO' ERR

require_commands() {
    local cmd
    for cmd in "${REQUIRED_CMDS[@]}"; do
        have_cmd "$cmd" || fatal "Missing required command: $cmd" "Missing dependency: $cmd"
    done
    [[ -f $THEME_CTL && -x $THEME_CTL ]] || fatal "Controller script missing/non-executable: $THEME_CTL"
}

# --- ROFI WRAPPERS ---
is_rofi_abort_exit() {
    local exit_code=$1
    [[ $exit_code -eq 1 || $exit_code -eq 130 || $exit_code -eq 143 ]] && return 0
    (( exit_code >= 10 && exit_code <= 28 ))
}

# Core interface engine
run_menu() {
    local prompt="$1"
    local allow_custom="$2"
    shift 2
    local options=("$@")
    local selected exit_code=0
    
    local -a rofi_cmd=(uwsm-app -- rofi -dmenu -i -p "$prompt" -theme-str "$ROFI_THEME_STR" -format s)
    [[ "$allow_custom" == "false" ]] && rofi_cmd+=("-no-custom")

    # If options were passed, pipe them into rofi. Otherwise, just open an empty input field.
    if (( ${#options[@]} > 0 )); then
        selected=$(printf '%s\n' "${options[@]}" | "${rofi_cmd[@]}") || exit_code=$?
    else
        selected=$("${rofi_cmd[@]}" </dev/null) || exit_code=$?
    fi

    if [[ $exit_code -eq 0 ]]; then
        printf "%s" "$selected"
        return 0
    fi

    # Return 1 triggers a graceful back/abort to the previous state loop
    if is_rofi_abort_exit "$exit_code"; then
        return 1 
    fi
    fatal "Rofi failed at '$prompt' with exit code $exit_code"
}

# --- STATE SYNC ---
get_current_state() {
    # Securely parse the live state without eval
    while IFS='=' read -r key val; do
        val="${val%\"}"; val="${val#\"}" # Strip surrounding quotes
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
    done < <("$THEME_CTL" get | grep -E '^[A-Z_]+=' 2>/dev/null || true)
}

# --- WIZARDS (STATE MACHINES) ---

wizard_theme() {
    local state="mode"
    local -A cfg=()
    local choice

    while true; do
        case "$state" in
            "mode")
                choice=$(run_menu "1/5 Mode [Cur: $CUR_MODE]" false "[X] Cancel" "${OPTS_MODE[@]}") || return 1
                if [[ "$choice" == "[X] Cancel" ]]; then return 1; fi
                cfg[mode]="$choice"
                state="type"
                ;;
            "type")
                choice=$(run_menu "2/5 Matugen Scheme [Cur: $CUR_TYPE]" false "[<-] Back" "${OPTS_SCHEME[@]}") || return 1
                if [[ "$choice" == "[<-] Back" ]]; then state="mode"; continue; fi
                cfg[type]="$choice"
                state="contrast"
                ;;
            "contrast")
                choice=$(run_menu "3/5 Contrast [Cur: $CUR_CONTRAST]" true "[<-] Back" "${OPTS_CONTRAST[@]}") || return 1
                if [[ "$choice" == "[<-] Back" ]]; then state="type"; continue; fi
                cfg[contrast]="$choice"
                state="index"
                ;;
            "index")
                choice=$(run_menu "4/5 Color Index [Cur: $CUR_INDEX]" false "[<-] Back" "${OPTS_INDEX[@]}") || return 1
                if [[ "$choice" == "[<-] Back" ]]; then state="contrast"; continue; fi
                cfg[index]="$choice"
                state="base16"
                ;;
            "base16")
                choice=$(run_menu "5/5 Base16 Backend [Cur: $CUR_BASE16]" false "[<-] Back" "${OPTS_BASE16[@]}") || return 1
                if [[ "$choice" == "[<-] Back" ]]; then state="index"; continue; fi
                cfg[base16]="$choice"
                state="apply"
                ;;
            "apply")
                notify normal "Applying Theme" "Mode: ${cfg[mode]} | Type: ${cfg[type]}"
                if ! "$THEME_CTL" set --no-wall \
                    --mode "${cfg[mode]}" \
                    --type "${cfg[type]}" \
                    --contrast "${cfg[contrast]}" \
                    --index "${cfg[index]}" \
                    --base16 "${cfg[base16]}"; then
                    log_error "Theme Backend Failed."
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
                choice=$(run_menu "1/6 Trans Type [Cur: $CUR_T_TYPE]" false "[X] Cancel" "${OPTS_TRANS_TYPE[@]}") || return 1
                if [[ "$choice" == "[X] Cancel" ]]; then return 1; fi
                cfg[type]="$choice"
                state="duration"
                ;;
            "duration")
                choice=$(run_menu "2/6 Duration (sec) [Cur: $CUR_T_DUR]" true "[<-] Back" "${OPTS_TRANS_DUR[@]}") || return 1
                if [[ "$choice" == "[<-] Back" ]]; then state="type"; continue; fi
                cfg[duration]="$choice"
                state="fps"
                ;;
            "fps")
                choice=$(run_menu "3/6 FPS [Cur: $CUR_T_FPS]" true "[<-] Back" "${OPTS_TRANS_FPS[@]}") || return 1
                if [[ "$choice" == "[<-] Back" ]]; then state="duration"; continue; fi
                cfg[fps]="$choice"
                state="bezier"
                ;;
            "bezier")
                choice=$(run_menu "4/6 Bezier Curve [Cur: $CUR_T_BEZ]" true "[<-] Back" "${OPTS_TRANS_BEZ[@]}") || return 1
                if [[ "$choice" == "[<-] Back" ]]; then state="fps"; continue; fi
                cfg[bezier]="$choice"
                state="angle"
                ;;
            "angle")
                choice=$(run_menu "5/6 Angle (Deg) [Cur: $CUR_T_ANG]" true "[<-] Back" "${OPTS_TRANS_ANG[@]}") || return 1
                if [[ "$choice" == "[<-] Back" ]]; then state="bezier"; continue; fi
                cfg[angle]="$choice"
                state="position"
                ;;
            "position")
                choice=$(run_menu "6/6 Position [Cur: $CUR_T_POS]" true "[<-] Back" "${OPTS_TRANS_POS[@]}") || return 1
                if [[ "$choice" == "[<-] Back" ]]; then state="angle"; continue; fi
                cfg[pos]="$choice"
                state="apply"
                ;;
            "apply")
                notify normal "Applying Animation" "Type: ${cfg[type]} | Dur: ${cfg[duration]}"
                if ! "$THEME_CTL" set --no-wall --no-regen \
                    --trans-type "${cfg[type]}" \
                    --trans-duration "${cfg[duration]}" \
                    --trans-fps "${cfg[fps]}" \
                    --trans-bezier "${cfg[bezier]}" \
                    --trans-angle "${cfg[angle]}" \
                    --trans-pos "${cfg[pos]}"; then
                    log_error "Animation Backend Failed."
                fi
                return 0
                ;;
        esac
    done
}

# --- SUBMENUS ---

submenu_regen() {
    local action="$1"
    local choice
    local -a opts=(
        "[<-] Back"
        "🎨 Yes (Regenerate Colors)"
        "🖼️ No (Just Change Wallpaper)"
    )

    choice=$(run_menu "Regenerate Colors?" false "${opts[@]}") || return 1

    case "$choice" in
        "[<-] Back"*) return 1 ;;
        "🎨"*) "$THEME_CTL" "$action" || log_error "Failed: $action"; return 0 ;;
        "🖼️"*) "$THEME_CTL" "$action" --no-regen || log_error "Failed: $action --no-regen"; return 0 ;;
    esac
}

submenu_solid_color() {
    local choice hex
    local -a opts=(
        "[<-] Back"
        "✏️ Custom Hex Code..."
    )

    # Populate preset colors logically mapped
    local k
    for k in "${OPTS_COLOR_KEYS[@]}"; do
        opts+=("$k (${OPTS_COLORS[$k]})")
    done

    while true; do
        choice=$(run_menu "Select Solid Color" false "${opts[@]}") || return 1

        case "$choice" in
            "[<-] Back"*) return 1 ;;
            "✏️"*)
                # Empty array passed to run_menu gives a clean UI with NO list items. Just a typing bar.
                # ESC cancels and continues the loop, bringing back the color preset list.
                hex=$(run_menu "Enter Hex (e.g. FF0000) [ESC to Cancel]" true) || continue
                if [[ -n "$hex" ]]; then
                    "$THEME_CTL" color "$hex" || log_error "Failed to apply color"
                    return 0
                fi
                ;;
            *)
                # Extract the hex code hidden in parenthesis (e.g. "🔴 Red (#FF0000)" -> "#FF0000")
                if [[ "$choice" =~ \((#[A-Fa-f0-9]{6})\) ]]; then
                    hex="${BASH_REMATCH[1]}"
                    "$THEME_CTL" color "$hex" || log_error "Failed to apply color"
                    return 0
                fi
                ;;
        esac
    done
}

submenu_wallpapers() {
    local choice
    local -a opts=(
        "[<-] Back to Main"
        "⏭️  Next Wallpaper"
        "⏮️  Prev Wallpaper"
        "🔀 Random Wallpaper"
        "🎨 Apply Solid Color"
    )

    while true; do
        choice=$(run_menu "Wallpaper Controls" false "${opts[@]}") || return 1

        # If a submenu returns 0 (Success), we return 0 to exit back to the Main Menu.
        # If a submenu returns 1 (User hit Back), `|| continue` safely reloads this exact menu.
        case "$choice" in
            "[<-] Back"*) return 1 ;;
            "⏭️"*) submenu_regen "next" && return 0 || continue ;;
            "⏮️"*) submenu_regen "prev" && return 0 || continue ;;
            "🔀"*) submenu_regen "random" && return 0 || continue ;;
            "🎨"*) submenu_solid_color && return 0 || continue ;;
        esac
    done
}

# --- MAIN LOOP ---
main() {
    require_commands

    local choice
    local -a main_opts=(
        "🎨 1. Theme Config Wizard (Matugen)"
        "✨ 2. Animation Config Wizard (awww)"
        "🖼️  3. Wallpaper & Color Controls"
        "🔄 4. Refresh Current Colors"
        "🧹 5. Reset Theme to Defaults"
        "❌ 6. Exit"
    )

    # Persistent execution loop. It only breaks entirely on 'Exit' or 'Escape'.
    while true; do
        get_current_state

        choice=$(run_menu "Dusky Theme Manager" false "${main_opts[@]}") || exit 0

        case "$choice" in
            "🎨"*) wizard_theme || true ;;
            "✨"*) wizard_animation || true ;;
            "🖼️"*) submenu_wallpapers || true ;;
            "🔄"*) "$THEME_CTL" refresh || log_error "Refresh failed" ;;
            "🧹"*) "$THEME_CTL" set --defaults || log_error "Reset failed" ;;
            "❌"*) exit 0 ;;
        esac
    done
}

main "$@"
