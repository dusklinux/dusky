#!/usr/bin/env bash

# -----------------------------------------------------------------------------
# Script: Hyprlock Theme Manager (htm)
# Description: Smart configuration management and symlinking for Hyprlock themes.
# Environment: Arch Linux / Hyprland
# Author: Elite DevOps Engineer
# -----------------------------------------------------------------------------

set -euo pipefail

# --- Configuration & Constants ---
readonly CONFIG_ROOT="${HOME}/.config/hypr"
readonly THEMES_ROOT="$CONFIG_ROOT/themes"
declare TOGGLE_MODE=false
declare PREVIEW_MODE=false

# Original state - populated after CONFIG_ROOT validation
declare ORIG_CONFIG=""

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

A theme manager for Hyprlock with text preview and toggle support.

Options:
  --toggle      Cycle to the next theme alphabetically without preview
  --preview     Show text preview of configs before selecting
  -h, --help    Show this help message and exit

Themes are discovered from: $THEMES_ROOT/<theme>/hyprlock.conf
EOF
}

# --- Argument Parsing ---
while (( $# > 0 )); do
    case "$1" in
        --toggle)
            TOGGLE_MODE=true
            ;;
        --preview)
            PREVIEW_MODE=true
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
    local -a deps=(hyprlock sed grep tput readlink)
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

# --- Cleanup Trap ---
cleanup() {
    local -i exit_code=$?

    # Restore cursor visibility
    tput cnorm 2>/dev/null || true

    exit "$exit_code"
}
trap cleanup EXIT INT TERM

# --- Discovery Phase ---
if [[ ! -d "$THEMES_ROOT" ]]; then
    log_err "Directory $THEMES_ROOT does not exist."
    exit 1
fi

# Capture original symlink only after we know the root exists
if [[ -L "${CONFIG_ROOT}/hyprlock.conf" ]]; then
    ORIG_CONFIG=$(readlink "${CONFIG_ROOT}/hyprlock.conf")
elif [[ -f "${CONFIG_ROOT}/hyprlock.conf" ]]; then
    ORIG_CONFIG="${CONFIG_ROOT}/hyprlock.conf"
fi

shopt -s nullglob
theme_dirs=("$THEMES_ROOT"/*/)
shopt -u nullglob

declare -a themes=()
declare -a theme_names=()

for dir in "${theme_dirs[@]}"; do
    dir="${dir%/}"
    if [[ -f "${dir}/hyprlock.conf" ]]; then
        themes+=("$dir")
        if [[ -f "$dir/theme.json" ]]; then 
          theme_names+=("$(jq .name "$dir/theme.json")${*/}")
        else
            theme_names+=("${dir##*/}")
        fi
    fi
done

if (( ${#themes[@]} == 0 )); then
    log_err "No valid theme directories found in $THEMES_ROOT (must contain hyprlock.conf)."
    exit 1
fi

declare -ir total=${#themes[@]}
declare -i selected_idx=0

# --- Preview Function ---
show_preview() {
    local theme_path="$1"
    local theme_name="$2"
    local config_file="${theme_path}/hyprlock.conf"
    
    printf '\033[H\033[2J'
    printf '%s=== Preview: %s ===%s\n\n' "$BOLD$C" "$theme_name" "$NC"
    
    # Show first 20 lines or entire file if shorter
    if [[ -f "$config_file" ]]; then
        head -n 20 "$config_file"
        local line_count
        line_count=$(wc -l < "$config_file")
        if (( line_count > 20 )); then
            printf '\n%s... (%d more lines)%s\n' "$Y" "$((line_count - 20))" "$NC"
        fi
    else
        printf '%sConfig file not found!%s\n' "$R" "$NC"
    fi
    
    printf '\n%s─────────────────────────────────────%s\n' "$C" "$NC"
}

# --- Logic Fork: Toggle vs Interactive ---
if [[ "$TOGGLE_MODE" == "true" ]]; then
    # 1. Resolve current config directory
    current_real_path=""
    if [[ -L "${CONFIG_ROOT}/hyprlock.conf" ]]; then
        current_real_path=$(readlink -f "${CONFIG_ROOT}/hyprlock.conf")
    elif [[ -f "${CONFIG_ROOT}/hyprlock.conf" ]]; then
        current_real_path=$(readlink -f "${CONFIG_ROOT}/hyprlock.conf")
    fi

    current_dir=""
    current_name="unknown"

    if [[ -n "$current_real_path" && -e "$current_real_path" ]]; then
        current_dir=$(dirname "$current_real_path")
    fi

    # 2. Find index of current theme
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

    # 3. Calculate next index
    if (( current_idx == -1 )); then
        # If current config doesn't match a known theme, start at 0
        selected_idx=0
    else
        selected_idx=$(( (current_idx + 1) % total ))
    fi

    log_info "Toggle mode: Switching from '${current_name}' to '${theme_names[selected_idx]}'"

else
    # --- Interactive Selection Mode ---
    tput civis 2>/dev/null || true

    while true; do
        if [[ "$PREVIEW_MODE" == "true" ]]; then
            show_preview "${themes[selected_idx]}" "${theme_names[selected_idx]}"
            printf '\n%sUse %sArrows/jk%s to browse, %sEnter%s to select, %sq%s to quit%s\n' \
                "$BOLD" "$Y" "$NC$BOLD" "$G" "$NC$BOLD" "$R" "$NC$BOLD" "$NC"
        else
            printf '\033[H\033[2J'
            printf '%sHyprlock Theme Selector%s (Use %sArrows/jk%s to browse, %sEnter%s to select, %sq%s to quit)\n\n' \
                "$BOLD" "$NC" "$Y" "$NC" "$G" "$NC" "$R" "$NC"

            for (( i = 0; i < total; i++ )); do 
                if (( i == selected_idx )); then
                    printf '%s> %s%s%s\n' "$C" "$BOLD" "${theme_names[i]}" "$NC"
                else
                    printf '  %s\n' "${theme_names[i]}"
                fi
            done
        fi

        IFS= read -rsn1 key || true
        if [[ "$key" == $'\x1b' ]]; then
            IFS= read -rsn2 -t 0.1 rest || true
            key+="${rest:-}"
        fi

        case "$key" in
            $'\x1b[A'|k)
                selected_idx=$(( (selected_idx - 1 + total) % total ))
                ;;
            $'\x1b[B'|j)
                selected_idx=$(( (selected_idx + 1) % total ))
                ;;
            '')
                # Enter pressed - confirm selection
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

readonly FINAL_THEME_DIR="${themes[selected_idx]}"
readonly FINAL_NAME="${theme_names[selected_idx]}"
readonly CONFIG_FILE="${FINAL_THEME_DIR}/hyprlock.conf"

if [[ "$TOGGLE_MODE" == "false" ]]; then
    printf '\n%sSelected Theme:%s %s\n' "$B" "$NC" "$FINAL_NAME"
fi

# --- Create Symlink ---
[[ "$TOGGLE_MODE" == "false" ]] && log_info "Creating symlink..."

rm -f "${CONFIG_ROOT}/hyprlock.conf"

ln -snf "${FINAL_THEME_DIR}/hyprlock.conf" "${CONFIG_ROOT}/hyprlock.conf"

if [[ "$TOGGLE_MODE" == "false" ]]; then
    log_success "Symlink: hyprlock.conf -> ${FINAL_THEME_DIR}/hyprlock.conf"
    log_success "Done! Your new Hyprlock theme is ready."
    log_info "Lock your screen to see the changes!"
fi

# Disable cleanup trap on success
trap - EXIT
exit 0
