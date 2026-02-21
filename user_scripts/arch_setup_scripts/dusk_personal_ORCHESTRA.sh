#!/usr/bin/env bash
# ==============================================================================
#  ARCH LINUX MASTER ORCHESTRATOR
# ==============================================================================
#  INSTRUCTIONS:
#  1. Configure SCRIPT_SEARCH_DIRS below with directories containing your scripts.
#  2. Edit the 'INSTALL_SEQUENCE' list below.
#  3. Use "S | name.sh" for Root (Sudo) commands.
#  4. Use "U | name.sh" for User commands.
#  5. Entries WITHOUT a / in the name are searched across SCRIPT_SEARCH_DIRS
#     in order (first match wins).
#  6. Entries WITH a / are treated as direct absolute paths (no searching).
#     Use ${HOME} instead of ~ for home directory paths.
# ==============================================================================

# --- USER CONFIGURATION AREA ---

# Directories to search for scripts (in order — first match wins)
SCRIPT_SEARCH_DIRS=(
    "${HOME}/user_scripts/arch_setup_scripts/scripts"
    # "${HOME}/my_other_scripts"
    # "/opt/shared_team_scripts"
)

# Delay (in seconds) after each successful script. Set to 0 to disable.
POST_SCRIPT_DELAY=0

INSTALL_SEQUENCE=(
    "U | 005_hypr_custom_config_setup.sh"
    "U | 010_package_removal.sh"
    "U | 015_set_thunar_terminal_kitty.sh"
    "U | 020_desktop_apps_username_setter.sh"
    "U | 025_configure_keyboard.sh"
    "U | 030_uwsm_env_comment_everything.sh"
    "U | 035_configure_uwsm_gpu.sh"
    "U | 040_long_sleep_timeout.sh"
    "S | 045_battery_limiter.sh"
    "S | 050_pacman_config.sh"
    "S | 055_pacman_reflector.sh"
    "S | 060_package_installation.sh"
    "U | 065_enabling_user_services.sh"
    "S | 070_openssh_setup.sh"
    "U | 075_changing_shell_zsh.sh"
    "S | 080_aur_paru_fallback_yay.sh"
    "S | 085_warp.sh"
    "U | 090_paru_packages_optional.sh"
    "S | 095_battery_limiter_again_dusk.sh"
    "U | 100_paru_packages.sh"
    "S | 110_aur_packages_sudo_services.sh"
    "U | 115_aur_packages_user_services.sh"
    "S | 120_create_mount_directories.sh"
    "S | 125_pam_keyring.sh"
    "U | 130_copy_service_files.sh"
    "U | 135_battery_notify_service.sh"
    "U | 140_fc_cache_fv.sh"
    "U | 145_matugen_directories.sh"
    "U | 150_wallpapers_download.sh"
    "U | 155_blur_shadow_opacity.sh"
    "U | 160_theme_ctl.sh set --defaults"
    "U | 165_qtct_config.sh"
    "U | 170_waypaper_config_reset.sh"
    "U | 175_animation_default.sh"
    "S | 180_udev_usb_notify.sh"
    "U | 185_terminal_default.sh"
    "S | 190_dusk_fstab.sh"
    "S | 195_firefox_symlink_parition.sh"
    "S | 200_tlp_config.sh"
    "S | 205_zram_configuration.sh"
    "S | 210_zram_optimize_swappiness.sh"
    "S | 215_powerkey_lid_close_behaviour.sh"
    "S | 220_logrotate_optimization.sh"
    "S | 225_faillock_timeout.sh"
    "U | 230_non_asus_laptop.sh"
    "U | 235_file_manager_switch.sh"
    "U | 240_swaync_dgpu_fix.sh"
    "S | 245_asusd_service_fix.sh"
    "S | 250_ftp_arch.sh"
    "U | 255_tldr_update.sh"
    "U | 260_spotify.sh"
    "U | 265_mouse_button_reverse.sh"
    "U | 280_dusk_clipboard_errands_delete.sh"
    "S | 285_tty_autologin.sh"
    "S | 290_system_services.sh"
    "S | 295_initramfs_optimization.sh"
    "U | 300_git_config.sh"
    "U | 305_new_github_repo_to_backup.sh"
    "U | 310_reconnect_and_push_new_changes_to_github.sh"
    "S | 315_grub_optimization.sh"
    "S | 320_systemdboot_optimization.sh"
    "S | 325_hosts_files_block.sh"
    "S | 330_gtk_root_symlink.sh"
    "S | 335_preload_config.sh"
    "U | 340_kokoro_cpu.sh"
    "U | 345_faster_whisper_cpu.sh"
    "S | 350_dns_systemd_resolve.sh"
    "U | 355_hyprexpo_plugin.sh"
    "U | 360_obsidian_pensive_vault_configure.sh"
    "U | 365_cache_purge.sh"
    "S | 370_arch_install_scripts_cleanup.sh"
    "U | 375_cursor_theme_bibata_classic_modern.sh"
    "S | 380_nvidia_open_source.sh"
    "S | 385_waydroid_setup.sh"
    "U | 390_clipboard_persistance.sh"
    "S | 395_intel_media_sdk_check.sh"
    "U | 400_firefox_matugen_pywalfox.sh"
    "U | 405_spicetify_matugen_setup.sh"
    "U | 410_waybar_swap_config.sh"
    "U | 415_mpv_setup.sh"
    "U | 420_kokoro_gpu_setup.sh" #requires nvidia gpu with at least 4gb vram
    "U | 425_parakeet_gpu_setup.sh" #requires nvidia gpu with at least 4gb vram
    "S | 430_btrfs_zstd_compression_stats.sh"
    "U | 435_key_sound_wayclick_setup.sh --setup"
    "U | 440_config_bat_notify.sh"
    "U | 450_generate_colorfiles_for_current_wallpaer.sh"
    "U | 455_hyprctl_reload.sh"
    "U | 460_switch_clipboard.sh"
    "S | 465_sddm_setup.sh"
    "U | 470_vesktop_matugen.sh"
    "U | 475_reverting_sleep_timeout.sh"
)

# ==============================================================================
#  INTERNAL ENGINE (Do not edit below unless you know Bash)
# ==============================================================================

# 1. Safety First
set -o errexit
set -o nounset
set -o pipefail

# 2. Paths & Constants
readonly STATE_FILE="${HOME}/Documents/.install_state"
readonly LOG_FILE="${HOME}/Documents/logs/install_$(date +%Y%m%d_%H%M%S).log"
readonly LOCK_FILE="/tmp/orchestra_${UID}.lock"
readonly SUDO_REFRESH_INTERVAL=50

# 3. Global Variables
declare -g SUDO_PID=""
declare -g LOGGING_INITIALIZED=0
declare -g EXECUTION_PHASE=0

# Bash 5.3 O(1) Performance Arrays
declare -gA COMPLETED_SCRIPTS=()
declare -gA SCRIPT_CACHE=()

# 4. Colors (Zero-Subshell ANSI Hardcodes)
declare -g RED="" GREEN="" BLUE="" YELLOW="" BOLD="" RESET=""

if [[ -t 1 ]]; then
    RED=$'\e[1;31m'
    GREEN=$'\e[1;32m'
    YELLOW=$'\e[1;33m'
    BLUE=$'\e[1;34m'
    BOLD=$'\e[1m'
    RESET=$'\e[0m'
fi

# 5. Logging
setup_logging() {
    local log_dir
    log_dir="$(dirname "$LOG_FILE")"
    if [[ ! -d "$log_dir" ]]; then
        mkdir -p "$log_dir" || { echo "CRITICAL ERROR: Could not create log directory $log_dir"; exit 1; }
    fi

    touch "$LOG_FILE"
    # PATCH: Close FD 9 for the tee process to avoid lock file inheritance
    exec > >(exec 9>&-; tee >(sed 's/\x1B\[[0-9;]*[a-zA-Z]//g; s/\x1B(B//g' >> "$LOG_FILE")) 2>&1

    LOGGING_INITIALIZED=1
    echo "--- Installation Started: $(date '+%Y-%m-%d %H:%M:%S') ---"
    echo "--- Log File: $LOG_FILE ---"
}

log() {
    local level="$1"
    local msg="$2"
    local color=""

    case "$level" in
        INFO)    color="$BLUE" ;;
        SUCCESS) color="$GREEN" ;;
        WARN)    color="$YELLOW" ;;
        ERROR)   color="$RED" ;;
        RUN)     color="$BOLD" ;;
    esac

    printf "%s[%s]%s %s\n" "${color}" "${level}" "${RESET}" "${msg}"
}

# 6. Sudo Management
init_sudo() {
    log "INFO" "Sudo privileges required. Please authenticate."
    if ! sudo -v; then
        log "ERROR" "Sudo authentication failed."
        exit 1
    fi

    # PATCH: Close FD 9 to prevent the sleep loop from holding the lock
    ( exec 9>&-; set +e; while true; do sudo -n true; sleep "$SUDO_REFRESH_INTERVAL"; kill -0 "$$" || exit; done 2>/dev/null ) &
    SUDO_PID=$!
    disown "$SUDO_PID"
}

cleanup() {
    local exit_code=$?
    if [[ -n "${SUDO_PID:-}" ]]; then
        kill "$SUDO_PID" 2>/dev/null || true
    fi

    if [[ $EXECUTION_PHASE -eq 1 ]]; then
        if [[ $exit_code -eq 0 ]]; then
            log "SUCCESS" "Orchestrator finished successfully."
        else
            log "ERROR" "Orchestrator exited with error code $exit_code."
        fi
    fi

    # Allow process substitution (tee/sed) to flush final output to log file
    if [[ $LOGGING_INITIALIZED -eq 1 ]]; then
        sleep 0.3
    fi
}
trap cleanup EXIT

# 7. Utility Functions
trim() {
    local var="$*"
    var="${var#"${var%%[![:space:]]*}"}"
    var="${var%"${var##*[![:space:]]}"}"
    printf '%s' "$var"
}

# O(1) Memory State Loader (STRICT MODE SAFE)
load_state() {
    # Safely wipe the associative array without losing the -A flag
    unset COMPLETED_SCRIPTS
    declare -gA COMPLETED_SCRIPTS=()

    # Only attempt to read if the file exists AND is > 0 bytes (-s)
    if [[ -s "$STATE_FILE" ]]; then
        # Explicitly declare array to prevent set -u unbound variable exceptions
        local _state_lines=()
        mapfile -t _state_lines < "$STATE_FILE" 2>/dev/null || true
        
        for _line in "${_state_lines[@]}"; do
            # Use proper if-statement to prevent set -e short-circuiting on blank lines
            if [[ -n "$_line" ]]; then
                COMPLETED_SCRIPTS["$_line"]=1
            fi
        done
    fi
}

resolve_script() {
    local name="$1"

    # O(1) Lookup: Check if we've already found this file
    if [[ -n "${SCRIPT_CACHE[$name]:-}" ]]; then
        printf '%s' "${SCRIPT_CACHE[$name]}"
        return 0
    fi

    # Contains a slash → direct path, no searching
    if [[ "$name" == */* ]]; then
        if [[ -f "$name" ]]; then
            SCRIPT_CACHE["$name"]="$name"
            printf '%s' "$name"
            return 0
        fi
        return 1
    fi
    # No slash → search directories in order, first match wins
    for dir in "${SCRIPT_SEARCH_DIRS[@]}"; do
        if [[ -f "${dir}/${name}" ]]; then
            SCRIPT_CACHE["$name"]="${dir}/${name}"
            printf '%s' "${dir}/${name}"
            return 0
        fi
    done
    return 1
}

report_search_locations() {
    local name="$1"
    if [[ "$name" == */* ]]; then
        log "ERROR" "Direct path not found: $name"
    else
        log "ERROR" "Script '$name' not found in any search directory:"
        for dir in "${SCRIPT_SEARCH_DIRS[@]}"; do
            log "ERROR" "  - ${dir}/"
        done
    fi
}

validate_search_dirs() {
    if [[ ${#SCRIPT_SEARCH_DIRS[@]} -eq 0 ]]; then
        log "ERROR" "SCRIPT_SEARCH_DIRS is empty. Add at least one directory."
        exit 1
    fi

    local valid=0
    for dir in "${SCRIPT_SEARCH_DIRS[@]}"; do
        if [[ -d "$dir" ]]; then
            log "INFO" "Search directory OK: $dir"
            ((++valid))
        else
            log "WARN" "Search directory not found: $dir"
        fi
    done

    if ((valid == 0)); then
        log "ERROR" "None of the configured search directories exist."
        exit 1
    fi
}

get_script_description() {
    local filepath="$1"
    local desc
    desc=$(sed -n '2s/^#[[:space:]]*//p' "$filepath" 2>/dev/null)
    if [[ -z "$desc" ]]; then
        desc=$(sed -n '3s/^#[[:space:]]*//p' "$filepath" 2>/dev/null)
    fi
    printf "%s" "${desc:-No description available}"
}

preflight_check() {
    local missing=0
    log "INFO" "Performing pre-flight validation..."

    for entry in "${INSTALL_SEQUENCE[@]}"; do
        local rest="${entry#*|}"
        rest=$(trim "$rest")
        local filename args
        read -r filename args <<< "$rest"

        if ! resolve_script "$filename" > /dev/null; then
            log "ERROR" "Missing: ${filename}"
            ((++missing))
        fi
    done

    if ((missing > 0)); then
        echo -e "${RED}CRITICAL:${RESET} $missing script(s) could not be found."
        read -r -p "Continue anyway? [y/N]: " _choice
        if [[ "${_choice,,}" != "y" ]]; then
            log "ERROR" "Aborting execution."
            exit 1
        fi
    else
        log "SUCCESS" "All sequence files verified and cached."
    fi
}

show_help() {
    cat << EOF
Arch Linux Master Orchestrator

Usage: $(basename "$0") [OPTIONS]

Options:
    --help, -h       Show this help message and exit
    --dry-run, -d    Preview execution plan without running anything
    --reset          Clear progress state and start fresh

Description:
    This script orchestrates the execution of multiple setup scripts
    for Arch Linux with Hyprland. It tracks completed scripts and
    can resume from where it left off if interrupted.

    Scripts are searched in the directories listed in SCRIPT_SEARCH_DIRS
    (first match wins). Entries with a / in the name are treated as
    direct absolute paths.

Examples:
    $(basename "$0")              # Normal run
    $(basename "$0") --dry-run    # Preview what would be executed
    $(basename "$0") --reset      # Reset progress and start over
EOF
    exit 0
}

main() {
    # Root User Guard
    if [[ $EUID -eq 0 ]]; then
        echo -e "${RED}CRITICAL ERROR: This script must NOT be run as root!${RESET}"
        echo "The script handles sudo privileges internally for specific steps."
        echo "Please run as a normal user: ./ORCHESTRA.sh"
        exit 1
    fi

    # --- READ-ONLY ARGUMENT HANDLING ---
    case "${1:-}" in
        --help|-h)
            show_help
            ;;
        --dry-run|-d)
            load_state
            echo -e "\n${YELLOW}=== DRY RUN MODE ===${RESET}"
            echo -e "State file: ${BOLD}${STATE_FILE}${RESET}\n"

            echo "Search directories:"
            for dir in "${SCRIPT_SEARCH_DIRS[@]}"; do
                if [[ -d "$dir" ]]; then
                    echo -e "  ${GREEN}✓${RESET} $dir"
                else
                    echo -e "  ${RED}✗${RESET} $dir ${RED}(not found)${RESET}"
                fi
            done
            echo ""

            echo "Execution plan:"
            echo ""

            local i=0
            local completed_count=0
            local missing_count=0

            for entry in "${INSTALL_SEQUENCE[@]}"; do
                ((++i))
                local mode="${entry%%|*}"
                local rest="${entry#*|}"
                mode=$(trim "$mode")
                rest=$(trim "$rest")

                local filename args
                read -r filename args <<< "$rest"

                local mode_label="USER"
                if [[ "$mode" == "S" ]]; then mode_label="SUDO"; fi

                local status=""

                if ! resolve_script "$filename" > /dev/null; then
                    status="${RED}[MISSING]${RESET}"
                    ((++missing_count))
                elif [[ -n "${COMPLETED_SCRIPTS[$filename]:-}" ]]; then
                    status="${GREEN}[DONE]${RESET}"
                    ((++completed_count))
                else
                    status="${BLUE}[PENDING]${RESET}"
                fi

                printf "  %3d. [%s] %-45s %s\n" "$i" "$mode_label" "${filename}${args:+ $args}" "$status"
            done

            echo ""
            echo -e "${BOLD}Summary:${RESET}"
            echo -e "  Total scripts: $i"
            echo -e "  Completed: ${GREEN}${completed_count}${RESET}"
            echo -e "  Pending: ${BLUE}$((i - completed_count - missing_count))${RESET}"
            if [[ $missing_count -gt 0 ]]; then echo -e "  Missing: ${RED}${missing_count}${RESET}"; fi
            echo ""
            echo "No changes were made."
            exit 0
            ;;
    esac

    # --- CONCURRENT EXECUTION GUARD ---
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo -e "${RED}ERROR: Another instance of this script is already running.${RESET}"
        exit 1
    fi

    # --- MUTATING ARGUMENT HANDLING ---
    case "${1:-}" in
        --reset)
            rm -f "$STATE_FILE"
            echo "State file reset. Starting fresh."
            ;;
        "")
            ;;
        *)
            echo -e "${RED}ERROR: Unknown option '${1}'${RESET}"
            echo "Use --help to see available options."
            exit 1
            ;;
    esac

    setup_logging
    validate_search_dirs
    preflight_check

    # Start timer
    local start_ts=$SECONDS

    # Check for sudo requirement
    local needs_sudo=0
    for entry in "${INSTALL_SEQUENCE[@]}"; do
        if [[ "$entry" == S* ]]; then needs_sudo=1; break; fi
    done

    if [[ $needs_sudo -eq 1 ]]; then
        init_sudo
    fi

    touch "$STATE_FILE"

    # --- SESSION RECOVERY PROMPT ---
    if [[ -s "$STATE_FILE" ]]; then
        echo -e "\n${YELLOW}>>> PREVIOUS SESSION DETECTED <<<${RESET}"
        read -r -p "Do you want to [C]ontinue where you left off or [S]tart over? [C/s]: " _session_choice
        if [[ "${_session_choice,,}" == "s" || "${_session_choice,,}" == "start" ]]; then
            rm -f "$STATE_FILE"
            touch "$STATE_FILE"
            log "INFO" "State file reset. Starting fresh."
        else
            log "INFO" "Continuing from previous session."
        fi
    fi

    # Load State into O(1) Memory Array
    load_state

    # --- EXECUTION MODE SELECTION ---
    local interactive_mode=0
    echo -e "\n${YELLOW}>>> EXECUTION MODE <<<${RESET}"
    read -r -p "Do you want to run interactively (prompt before every script)? [y/N]: " _mode_choice
    if [[ "${_mode_choice,,}" == "y" || "${_mode_choice,,}" == "yes" ]]; then
        interactive_mode=1
        log "INFO" "Interactive mode selected. You will be asked before each script."
    else
        log "INFO" "Autonomous mode selected. Running all scripts without confirmation."
    fi

    local total_scripts=${#INSTALL_SEQUENCE[@]}
    local current_index=0
    log "INFO" "Processing ${total_scripts} scripts..."

    local SKIPPED_OR_FAILED=()

    EXECUTION_PHASE=1

    for entry in "${INSTALL_SEQUENCE[@]}"; do
        ((++current_index))

        local mode="${entry%%|*}"
        local rest="${entry#*|}"

        mode=$(trim "$mode")
        rest=$(trim "$rest")

        # Separate filename from arguments
        local filename args
        read -r filename args <<< "$rest"

        # --- RESOLVE SCRIPT PATH ---
        local script_path=""
        while true; do
            if script_path=$(resolve_script "$filename"); then
                break
            fi
            report_search_locations "$filename"
            echo -e "${YELLOW}Action Required:${RESET} File is missing."
            read -r -p "Do you want to [S]kip to next, [R]etry check, or [Q]uit? (s/r/q): " _choice

            case "${_choice,,}" in
                s|skip)
                    log "WARN" "Skipping $filename (User Selection)"
                    SKIPPED_OR_FAILED+=("$filename")
                    continue 2
                    ;;
                r|retry)
                    log "INFO" "Retrying check for $filename..."
                    sleep 1
                    ;;
                *)
                    log "INFO" "Stopping execution. Place the script in one of the search directories and rerun."
                    exit 1
                    ;;
            esac
        done

        # --- STATE FILE SKIP CHECK (O(1) Array Lookup) ---
        if [[ -n "${COMPLETED_SCRIPTS[$filename]:-}" ]]; then
            log "WARN" "[${current_index}/${total_scripts}] Skipping $filename (Already Completed)"
            continue
        fi

        # --- USER CONFIRMATION PROMPT (CONDITIONAL) ---
        if [[ $interactive_mode -eq 1 ]]; then
            local desc
            desc=$(get_script_description "$script_path")

            echo -e "\n${YELLOW}>>> NEXT SCRIPT [${current_index}/${total_scripts}]:${RESET} $filename${args:+ $args} ($mode)"
            echo -e "    ${BOLD}Description:${RESET} $desc"

            read -r -p "Do you want to [P]roceed, [S]kip, or [Q]uit? (p/s/q): " _user_confirm
            case "${_user_confirm,,}" in
                s|skip)
                    log "WARN" "Skipping $filename (User Selection)"
                    SKIPPED_OR_FAILED+=("$filename")
                    continue
                    ;;
                q|quit)
                    log "INFO" "User requested exit."
                    exit 0
                    ;;
                *)
                    # Fall through to execution
                    ;;
            esac
        fi

        # --- EXECUTION RETRY LOOP ---
        while true; do
            log "RUN" "[${current_index}/${total_scripts}] Executing: ${filename}${args:+ $args} ($mode)"

            local result=0
            set -f
            if [[ "$mode" == "S" ]]; then
                (cd "$(dirname "$script_path")" && sudo bash "$(basename "$script_path")" $args) || result=$?
            elif [[ "$mode" == "U" ]]; then
                (cd "$(dirname "$script_path")" && bash "$(basename "$script_path")" $args) || result=$?
            else
                log "ERROR" "Invalid mode '$mode' in config. Use 'S' or 'U'."
                exit 1
            fi
            set +f

            if [[ $result -eq 0 ]]; then
                echo "$filename" >> "$STATE_FILE"
                COMPLETED_SCRIPTS["$filename"]=1 # Update Memory Array instantly
                log "SUCCESS" "Finished $filename"
                if [[ "$POST_SCRIPT_DELAY" != "0" ]]; then
                    sleep "$POST_SCRIPT_DELAY"
                fi
                break
            else
                log "ERROR" "Failed $filename (Exit Code: $result)."

                echo -e "${YELLOW}Action Required:${RESET} Script execution failed."
                read -r -p "Do you want to [S]kip to next, [R]etry, or [Q]uit? (s/r/q): " _fail_choice

                case "${_fail_choice,,}" in
                    s|skip)
                        log "WARN" "Skipping $filename (User Selection). NOT marking as complete."
                        SKIPPED_OR_FAILED+=("$filename")
                        break
                        ;;
                    r|retry)
                        log "INFO" "Retrying $filename..."
                        sleep 1
                        continue
                        ;;
                    *)
                        log "INFO" "Stopping execution as requested."
                        exit 1
                        ;;
                esac
            fi
        done
    done

    # --- SUMMARY OF FAILED / SKIPPED SCRIPTS ---
    if [[ ${#SKIPPED_OR_FAILED[@]} -gt 0 ]]; then
        echo -e "\n${YELLOW}================================================================${RESET}"
        echo -e "${YELLOW}NOTE: Some scripts were skipped or failed:${RESET}"
        for f in "${SKIPPED_OR_FAILED[@]}"; do
            echo " - $f"
        done
        echo -e "\nYou can run them individually from their respective directories:"
        for dir in "${SCRIPT_SEARCH_DIRS[@]}"; do
            if [[ -d "$dir" ]]; then echo -e "  ${BOLD}${dir}/${RESET}"; fi
        done
        echo -e "${YELLOW}================================================================${RESET}\n"
    fi

    # Calculate elapsed time
    local end_ts=$SECONDS
    local duration=$((end_ts - start_ts))
    local minutes=$((duration / 60))
    local seconds=$((duration % 60))

    # --- COMPLETION & REBOOT NOTICE ---
    echo -e "\n${GREEN}================================================================${RESET}"
    echo -e "${BOLD}FINAL INSTRUCTIONS:${RESET}"
    echo -e "1. Execution Time: ${BOLD}${minutes}m ${seconds}s${RESET}"
    echo -e "2. Please ${BOLD}REBOOT YOUR SYSTEM${RESET} for all changes to take effect."
    echo -e "3. This script is designed to be run multiple times."
    echo -e "   If you think something wasn't done right, you can run this script again."
    echo -e "   It will ${BOLD}NOT${RESET} re-download the whole thing again, but instead"
    echo -e "   only download/configure what might have failed the first time."
    echo -e "${GREEN}================================================================${RESET}\n"
}

main "$@"
