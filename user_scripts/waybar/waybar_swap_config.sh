#!/usr/bin/env bash
# Waybar Theme Manager (wtm)
# Description: Live preview, smart configuration, and symlinking for Waybar themes.
# Environment: Arch Linux / Hyprland / UWSM
# Author: Elite DevOps Engineer
# -----------------------------------------------------------------------------

set -euo pipefail

# --- Configuration & Constants ---
readonly CONFIG_ROOT="${HOME}/.config/waybar"
declare -ra UWSM_CMD=(uwsm-app --)
declare -i PREVIEW_PID=0
declare TOGGLE_MODE=false
declare TUI_ACTIVE=false
declare FINALIZED=false

# Original state - populated after CONFIG_ROOT validation
declare ORIG_CONFIG=""
declare ORIG_STYLE=""

# --- Colors (ANSI-C Quoting) ---
readonly R=$'\033[0;31m'
readonly G=$'\033[0;32m'
readonly B=$'\033[0;34m'
readonly Y=$'\033[1;33m'
readonly C=$'\033[0;36m'
readonly NC=$'\033[0m'
readonly BOLD=$'\033[1m'

# --- Helper Functions ---
log_info()    { printf '%s[INFO]%s %s\n' "$B" "$NC" "$*"; }
log_success() { printf '%s[SUCCESS]%s %s\n' "$G" "$NC" "$*"; }
log_warn()    { printf '%s[WARN]%s %s\n' "$Y" "$NC" "$*" >&2; }
log_err()     { printf '%s[ERROR]%s %s\n' "$R" "$NC" "$*" >&2; }

usage() {
    cat <<EOF
Usage: ${0##*/} [OPTIONS]

A TUI theme manager for Waybar with live preview.

Options:
  --toggle      Cycle to the next theme alphabetically without TUI
  -h, --help    Show this help message and exit

Themes are discovered from: $CONFIG_ROOT/<theme>/config.jsonc
EOF
}

# --- Argument Parsing ---
while (( $# > 0 )); do
    case "$1" in
        --toggle)
            TOGGLE_MODE=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_err "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
    shift
done

# --- Pre-flight Checks ---
if (( EUID == 0 )); then
    log_err "This script modifies user configurations and must not be run as root."
    exit 1
fi

if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
    log_err "No Wayland display detected. This script requires an active Wayland session."
    exit 1
fi

# --- Dependency Check ---
check_deps() {
    local -a deps=(waybar uwsm-app sed grep tput pkill pgrep readlink stty setsid)
    local -a missing=()

    for cmd in "${deps[@]}"; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done

    if (( ${#missing[@]} > 0 )); then
        log_err "Missing dependencies: ${missing[*]}"
        exit 1
    fi
}
check_deps

# --- Utility: Kill Waybar with Timeout ---
kill_waybar() {
    pkill -x waybar 2>/dev/null || true
    local -i retries=0
    while pgrep -x waybar &>/dev/null; do
        sleep 0.1
        (( ++retries ))
        if (( retries > 20 )); then
            log_warn "Waybar refused to close gracefully, forcing kill..."
            pkill -9 -x waybar 2>/dev/null || true
            sleep 0.1
            break
        fi
    done
}

# --- Cleanup Trap ---
cleanup() {
    local -i exit_code=$?

    # Skip cleanup if we've successfully finalized
    if [[ "$FINALIZED" == "true" ]]; then
        tput cnorm 2>/dev/null || true
        exit "$exit_code"
    fi

    # Kill preview wrapper if running
    if (( PREVIEW_PID > 0 )) && kill -0 "$PREVIEW_PID" 2>/dev/null; then
        kill "$PREVIEW_PID" 2>/dev/null || true
        wait "$PREVIEW_PID" 2>/dev/null || true
    fi

    # Ensure waybar is definitely gone on exit
    pkill -x waybar 2>/dev/null || true

    # Restore original symlinks only if TUI was active
    if [[ "$TUI_ACTIVE" == "true" && -n "$ORIG_CONFIG" ]]; then
        rm -f "${CONFIG_ROOT}/config.jsonc" "${CONFIG_ROOT}/style.css"
        ln -snf "$ORIG_CONFIG" "${CONFIG_ROOT}/config.jsonc"
        [[ -n "$ORIG_STYLE" ]] && ln -snf "$ORIG_STYLE" "${CONFIG_ROOT}/style.css"
    fi

    # Restore cursor visibility
    tput cnorm 2>/dev/null || true

    exit "$exit_code"
}
trap cleanup EXIT INT TERM

# --- Discovery Phase ---
if [[ ! -d "$CONFIG_ROOT" ]]; then
    log_err "Directory $CONFIG_ROOT does not exist."
    exit 1
fi

# Capture original symlinks
if [[ -L "${CONFIG_ROOT}/config.jsonc" ]]; then
    ORIG_CONFIG=$(readlink "${CONFIG_ROOT}/config.jsonc")
fi
if [[ -L "${CONFIG_ROOT}/style.css" ]]; then
    ORIG_STYLE=$(readlink "${CONFIG_ROOT}/style.css")
fi

shopt -s nullglob
theme_dirs=("$CONFIG_ROOT"/*/)
shopt -u nullglob

declare -a themes=()
declare -a theme_names=()

for dir in "${theme_dirs[@]}"; do
    dir="${dir%/}"
    if [[ -f "${dir}/config.jsonc" ]]; then
        themes+=("$dir")
        theme_names+=("${dir##*/}")
    fi
done

if (( ${#themes[@]} == 0 )); then
    log_err "No valid theme directories found in $CONFIG_ROOT (must contain config.jsonc)."
    exit 1
fi

declare -ir total=${#themes[@]}
declare -i selected_idx=0

# --- Logic Fork: Toggle vs TUI ---
if [[ "$TOGGLE_MODE" == "true" ]]; then
    current_real_path=""
    if [[ -L "${CONFIG_ROOT}/config.jsonc" ]]; then
        current_real_path=$(readlink -f "${CONFIG_ROOT}/config.jsonc")
    elif [[ -f "${CONFIG_ROOT}/config.jsonc" ]]; then
        current_real_path=$(readlink -f "${CONFIG_ROOT}/config.jsonc")
    fi

    current_dir=""
    current_name="unknown"

    if [[ -n "$current_real_path" && -e "$current_real_path" ]]; then
        current_dir=$(dirname "$current_real_path")
    fi

    declare -i current_idx=-1
    if [[ -n "$current_dir" ]]; then
        for (( i = 0; i < total; i++ )); do
            theme_real_path=$(readlink -f "${themes[i]}")
            if [[ "$theme_real_path" == "$current_dir" ]]; then
                current_idx=$i
                current_name="${theme_names[i]}"
                break
            fi
        done
    fi

    if (( current_idx == -1 )); then
        selected_idx=0
    else
        selected_idx=$(( (current_idx + 1) % total ))
    fi

    log_info "Toggle mode: Switching from '${current_name}' to '${theme_names[selected_idx]}'"

else
    # --- TUI Mode ---
    TUI_ACTIVE=true

    start_preview() {
        local theme_path="$1"

        rm -f "${CONFIG_ROOT}/config.jsonc" "${CONFIG_ROOT}/style.css"
        ln -snf "${theme_path}/config.jsonc" "${CONFIG_ROOT}/config.jsonc"
        [[ -f "${theme_path}/style.css" ]] && \
            ln -snf "${theme_path}/style.css" "${CONFIG_ROOT}/style.css"

        if (( PREVIEW_PID > 0 )) && kill -0 "$PREVIEW_PID" 2>/dev/null; then
            kill "$PREVIEW_PID" 2>/dev/null || true
            wait "$PREVIEW_PID" 2>/dev/null || true
        fi

        kill_waybar

        "${UWSM_CMD[@]}" waybar &>/dev/null &
        PREVIEW_PID=$!
        sleep 0.3
    }

    tput civis 2>/dev/null || true
    start_preview "${themes[selected_idx]}"

    while true; do
        printf '\033[H\033[2J'
        printf '%sWaybar Theme Selector%s (Use %sArrows/jk%s to browse, %sEnter%s to select, %sq%s to quit)\n\n' \
            "$BOLD" "$NC" "$Y" "$NC" "$G" "$NC" "$R" "$NC"

        for (( i = 0; i < total; i++ )); do
            if (( i == selected_idx )); then
                printf '%s> %s%s%s\n' "$C" "$BOLD" "${theme_names[i]}" "$NC"
            else
                printf '  %s\n' "${theme_names[i]}"
            fi
        done

        IFS= read -rsn1 key || true
        if [[ "$key" == $'\x1b' ]]; then
            IFS= read -rsn2 -t 0.1 rest || true
            key+="${rest:-}"
        fi

        case "$key" in
            $'\x1b[A'|k)
                selected_idx=$(( (selected_idx - 1 + total) % total ))
                start_preview "${themes[selected_idx]}"
                ;;
            $'\x1b[B'|j)
                selected_idx=$(( (selected_idx + 1) % total ))
                start_preview "${themes[selected_idx]}"
                ;;
            '')
                TUI_ACTIVE=false
                break
                ;;
            q|Q)
                log_info "Selection cancelled."
                exit 0
                ;;
        esac
    done
    tput cnorm 2>/dev/null || true
fi

# --- Finalization Phase (Common) ---

if (( PREVIEW_PID > 0 )) && kill -0 "$PREVIEW_PID" 2>/dev/null; then
    kill "$PREVIEW_PID" 2>/dev/null || true
    wait "$PREVIEW_PID" 2>/dev/null || true
fi

kill_waybar

readonly FINAL_THEME_DIR="${themes[selected_idx]}"
readonly FINAL_NAME="${theme_names[selected_idx]}"
readonly CONFIG_FILE="${FINAL_THEME_DIR}/config.jsonc"

if [[ "$TOGGLE_MODE" == "false" ]]; then
    printf '\n%sSelected Theme:%s %s\n' "$B" "$NC" "$FINAL_NAME"
fi

# --- Smart Position Detection & Adjustment ---
if [[ "$TOGGLE_MODE" == "false" ]]; then
    current_pos=""
    if current_pos_line=$(grep -m1 -E '"position"[[:space:]]*:[[:space:]]*"(top|bottom|left|right)"' "$CONFIG_FILE" 2>/dev/null); then
        current_pos=$(sed -E 's/.*"position"[[:space:]]*:[[:space:]]*"([a-z]+)".*/\1/' <<< "$current_pos_line")
    fi

    target_pos=""
    if [[ -z "$current_pos" ]]; then
        log_warn "Could not detect 'position' in config.jsonc. Skipping position adjustment."
    else
        case "$current_pos" in
            top|bottom)
                printf 'Detected %sHorizontal%s bar (currently: %s).\n' "$Y" "$NC" "$current_pos"
                printf 'Where do you want it? [t]op / [b]ottom (Enter to keep): '
                IFS= read -rn1 choice || choice=""
                printf '\n'
                case "${choice}" in
                    t|T) target_pos="top" ;;
                    b|B) target_pos="bottom" ;;
                    *)   log_info "Keeping original position." ;;
                esac
                ;;
            left|right)
                printf 'Detected %sVertical%s bar (currently: %s).\n' "$Y" "$NC" "$current_pos"
                printf 'Where do you want it? [l]eft / [r]ight (Enter to keep): '
                IFS= read -rn1 choice || choice=""
                printf '\n'
                case "${choice}" in
                    l|L) target_pos="left" ;;
                    r|R) target_pos="right" ;;
                    *)   log_info "Keeping original position." ;;
                esac
                ;;
        esac
    fi

    if [[ -n "$target_pos" && "$target_pos" != "$current_pos" ]]; then
        log_info "Updating config position to '$target_pos'..."
        sed -i -E "s/(\"position\"[[:space:]]*:[[:space:]]*)\"[^\"]+\"/\1\"${target_pos}\"/" "$CONFIG_FILE"
        log_success "Position updated."
    fi
fi

# --- Create Symlinks ---
[[ "$TOGGLE_MODE" == "false" ]] && log_info "Creating symlinks..."

rm -f "${CONFIG_ROOT}/config.jsonc" "${CONFIG_ROOT}/style.css"

ln -snf "${FINAL_THEME_DIR}/config.jsonc" "${CONFIG_ROOT}/config.jsonc"
[[ "$TOGGLE_MODE" == "false" ]] && \
    log_success "Symlink: config.jsonc -> ${FINAL_THEME_DIR}/config.jsonc"

if [[ -f "${FINAL_THEME_DIR}/style.css" ]]; then
    ln -snf "${FINAL_THEME_DIR}/style.css" "${CONFIG_ROOT}/style.css"
    [[ "$TOGGLE_MODE" == "false" ]] && \
        log_success "Symlink: style.css -> ${FINAL_THEME_DIR}/style.css"
elif [[ "$TOGGLE_MODE" == "false" ]]; then
    log_warn "No style.css found. Only config.jsonc was linked."
fi

# --- Start Final Waybar ---
[[ "$TOGGLE_MODE" == "false" ]] && log_info "Starting Waybar via UWSM..."

FINALIZED=true
trap - EXIT INT TERM

stty sane 2>/dev/null || true

setsid --fork "${UWSM_CMD[@]}" waybar </dev/null &>/dev/null

sleep 0.5

[[ "$TOGGLE_MODE" == "false" ]] && log_success "Done. Enjoy your new setup!"

exit 0
