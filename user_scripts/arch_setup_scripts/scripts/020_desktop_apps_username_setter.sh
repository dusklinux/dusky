#!/usr/bin/env bash
# ==============================================================================
#  Arch Linux Desktop Entry Path Fixer
#  Environment: Hyprland / UWSM
#  Description: Surgical update of 'Exec' paths in .desktop files to match $USER.
#  Version: 2.1.0
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. Configuration & Safety
# ------------------------------------------------------------------------------
set -euo pipefail

# Cleanup trap - only shows message on interrupt/error signals
cleanup() {
    local -ri code=$?
    # If exit code is > 128, it was a signal (interrupt/kill)
    if (( code > 128 )); then
        printf '\n%s⚠ Script Interrupted (Signal %d)%s\n' \
            "${C_YELLOW:-}" "$((code - 128))" "${C_RESET:-}" >&2
    fi
}
trap cleanup EXIT

# Detect color support (TTY check)
if [[ -t 1 && -z ${NO_COLOR:-} ]]; then
    readonly C_RESET=$'\033[0m'
    readonly C_BOLD=$'\033[1m'
    readonly C_GREEN=$'\033[32m'
    readonly C_BLUE=$'\033[34m'
    readonly C_YELLOW=$'\033[33m'
    readonly C_RED=$'\033[31m'
    readonly C_GRAY=$'\033[90m'
else
    readonly C_RESET='' C_BOLD='' C_GREEN='' C_BLUE=''
    readonly C_YELLOW='' C_RED='' C_GRAY=''
fi

# Target Directory
readonly TARGET_DIR="${HOME}/.local/share/applications"

# Validate USER environment variable
if [[ -z ${USER:-} ]]; then
    printf '%s✖ Error: USER environment variable is not set.%s\n' "${C_RED}" "${C_RESET}" >&2
    exit 1
fi
readonly CURRENT_USER="$USER"

# Prepare USER variable for SED (Escape slashes and ampersands)
# This prevents sed from breaking if the username is exotic
readonly USER_SED_SAFE="${CURRENT_USER//\//\\/}"

# ------------------------------------------------------------------------------
# 2. Target Files List
#    Comment out lines (using #) to exclude specific files from processing.
# ------------------------------------------------------------------------------
readonly TARGET_FILES=(
    "airmon_ng.desktop"
    "asus_control.desktop"
    "btrfs_compression_stats.desktop"
    "brightness_slider.desktop"
    "cache_purge.desktop"
    "clipboard_persistance.desktop"
    "dusky_appearances.desktop"
    "dusky_battery_notify.desktop"
    "dusky_control_center.desktop"
    "dusky_hyprlock_switcher.desktop"
    "dusky_hypridle_timeout.desktop"
    "dusky_hypridle_toggle.desktop"
    "dusky_input.desktop"
    "dusky_keybinds.desktop"
    "dusky_monitor.desktop"
    "dusky_matugen_presets.desktop"
    "dusky_power.desktop"
    "dusky_waybars.desktop"
    "dusky_window_rules.desktop"
    "dusky_site_blocker.desktop"
    "dusky_swaync_side.desktop"
    "dusky_service_toggle.desktop"
    "file_switcher.desktop"
    "google_image_search.desktop"
    "hyprsunset_slider.desktop"
    "IO_Monitor.desktop"
    "iphone_vnc.desktop"
    "matugen.desktop"
    "mouse_button_reverse.desktop"
    "music_recognition.desktop"
    "new_github_repo.desktop"
    "ollama_terminal.desktop"
    "opacity_blur_shadow.desktop"
    "openssh.desktop"
    "powersave.desktop"
    "powersave_off.desktop"
    "process_terminator.desktop"
    "relink_github_repo.desktop"
    "rotate_screen_clockwise.desktop"
    "rotate_screen_counter_clockwise.desktop"
    "rofi_emoji.desktop"
    "rofi_calculator.desktop"
    "rofi_wallpaper_selector.desktop"
    "scale_down.desktop"
    "scale_up.desktop"
    "sysbench_benchmark.desktop"
    "tailscale_setup.desktop"
    "tailscale_uninstall.desktop"
    "volume_slider.desktop"
    "update_dusky.desktop"
    "warp.desktop"
    "wayclick.desktop"
)

# ------------------------------------------------------------------------------
# 3. Helper Functions
# ------------------------------------------------------------------------------

log_info() {
    printf '%s::%s %s%s\n' "$C_BLUE" "$C_BOLD" "$1" "$C_RESET"
}

log_success() {
    printf '%s✔%s %s\n' "$C_GREEN" "$C_RESET" "$1"
}

log_skip() {
    printf '%s•%s %s %s(No changes needed)%s\n' \
        "$C_GRAY" "$C_RESET" "$1" "$C_GRAY" "$C_RESET"
}

log_warn() {
    printf '%s⚠%s %s\n' "$C_YELLOW" "$C_RESET" "$1"
}

log_error() {
    printf '%s✖ Error:%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
}

# Check if file's Exec/TryExec lines contain paths needing update
# Returns: 0 (True) if update needed, 1 (False) otherwise
file_needs_update() {
    local filepath="$1"
    local current_lines simulated_lines

    # 1. Grab only lines starting with Exec= or TryExec= that contain /home/
    #    We redirect stderr to null to suppress noise if file is empty
    current_lines=$(grep -E '^(Exec|TryExec)=.*/home/[^/]+/' "$filepath" 2>/dev/null) || return 1

    # 2. Simulate the replacement in memory
    #    We replace /home/<ANY_USER>/ with /home/<CURRENT_USER>/
    simulated_lines=$(sed -E "s|/home/[^/]+/|/home/${USER_SED_SAFE}/|g" <<< "$current_lines")

    # 3. If the simulation differs from reality, we need to update
    [[ "$current_lines" != "$simulated_lines" ]]
}

# ------------------------------------------------------------------------------
# 4. Main Logic
# ------------------------------------------------------------------------------
main() {
    # Check for quiet flag immediately
    # Redirects stdout (FD 1) to /dev/null if flag is present.
    # Stderr (FD 2) remains active for critical errors.
    if [[ "${1:-}" == "--quiet" ]]; then
        exec 1>/dev/null
    fi

    local filename filepath
    local -i updated_count=0 skipped_count=0 missing_count=0

    # Clean start
    [[ -t 1 ]] && command -v clear &>/dev/null && clear
    
    printf '%s%sArch Linux Desktop Path Fixer%s\n' "$C_BOLD" "$C_BLUE" "$C_RESET"
    printf '%s━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━%s\n' "$C_GRAY" "$C_RESET"
    printf '%sTarget Directory:%s %s\n' "$C_GRAY" "$C_RESET" "$TARGET_DIR"
    printf '%sTarget User:%s      %s%s%s\n\n' \
        "$C_GRAY" "$C_RESET" "$C_BOLD" "$CURRENT_USER" "$C_RESET"

    if [[ ! -d "$TARGET_DIR" ]]; then
        log_error "Directory not found: $TARGET_DIR"
        exit 1
    fi

    # Loop through the user-defined list
    for filename in "${TARGET_FILES[@]}"; do
        filepath="${TARGET_DIR}/${filename}"

        # 1. Check existence
        if [[ ! -f "$filepath" ]]; then
            log_warn "File not found: ${filename}"
            ((++missing_count)) || true # prevent set -e exit on 0
            continue
        fi

        # 2. Check & Update
        if file_needs_update "$filepath"; then
            # Perform the actual sed update
            # -i: edit in place
            # -E: extended regex
            # Only match lines starting with Exec= or TryExec=
            # Regex: Find /home/<not-a-slash>+/ and replace with /home/<current-user>/
            sed -i -E "/^(Exec|TryExec)=/s|/home/[^/]+/|/home/${USER_SED_SAFE}/|g" "$filepath"
            
            log_success "Updated: ${filename}"
            ((++updated_count)) || true
        else
            log_skip "${filename}"
            ((++skipped_count)) || true
        fi
    done

    # 5. Summary
    printf '\n%s%sSummary%s\n' "$C_BOLD" "$C_BLUE" "$C_RESET"
    printf '%s━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━%s\n' "$C_GRAY" "$C_RESET"
    printf '  %s✔ Updated:%s  %d\n' "$C_GREEN" "$C_RESET" "$updated_count"
    printf '  %s• Skipped:%s  %d\n' "$C_GRAY" "$C_RESET" "$skipped_count"
    
    if (( missing_count > 0 )); then
        printf '  %s⚠ Missing:%s  %d\n' "$C_YELLOW" "$C_RESET" "$missing_count"
    fi
    printf '\n'
}

main "$@"
