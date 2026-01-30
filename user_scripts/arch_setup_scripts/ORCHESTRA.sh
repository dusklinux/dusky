#!/usr/bin/env bash
# ==============================================================================
#  ARCH LINUX MASTER ORCHESTRATOR - FINAL (RESTORED)
# ==============================================================================
#  INSTRUCTIONS:
#  1. Edit the 'INSTALL_SEQUENCE' list below.
#  2. Use "S | name.sh" for Root (Sudo) commands.
#  3. Use "U | name.sh" for User commands.
# ==============================================================================

# --- USER CONFIGURATION AREA ---

INSTALL_SEQUENCE=(
    "U | 000_configure_keyboard.sh"
    "U | 000_uwsm_env_comment.sh --auto"
#    "U | 000_configure_uwsm_gpu.sh"
    "U | 001_long_sleep_timeout.sh"
#    "S | 002_battery_limiter.sh"
    "S | 003_pacman_config.sh"
    "S | 004_pacman_reflector.sh"
    "S | 005_package_installation.sh"
    "U | 006_enabling_user_services.sh"
    "S | 007_openssh_setup.sh"
    "U | 008_changing_shell_zsh.sh"
    "S | 009_aur_paru_fallback_yay.sh"
#    "S | 010_warp.sh"
    "U | 011_paru_packages_optional.sh"
#    "S | 012_battery_limiter_again_dusk.sh"
    "U | 013_paru_packages.sh"
    "S | 014_aur_packages_sudo_services.sh"
    "U | 015_aur_packages_user_services.sh"
#    "S | 016_create_mount_directories.sh"
    "S | 017_pam_keyring.sh"
    "U | 018_copy_service_files.sh --default"
    "U | 019_battery_notify_service.sh"
    "U | 020_fc_cache_fv.sh"
    "U | 021_matugen_directories.sh"
    "U | 022_wallpapers_download.sh"
    "U | 023_blur_shadow_opacity.sh"
    "U | 024_theme_ctl.sh set --defaults"
    "U | 025_qtct_config.sh"
    "U | 026_waypaper_config_reset.sh"
    "U | 027_animation_default.sh"
    "S | 028_udev_usb_notify.sh"
    "U | 029_terminal_default.sh"
#    "S | 030_dusk_fstab.sh"
#    "S | 031_firefox_symlink_parition.sh"
#    "S | 032_tlp_config.sh"
    "S | 033_zram_configuration.sh"
#    "S | 034_zram_optimize_swappiness.sh"
#    "S | 035_powerkey_lid_close_behaviour.sh"
    "S | 036_logrotate_optimization.sh"
#    "S | 037_faillock_timeout.sh"
    "U | 038_non_asus_laptop.sh --auto"
    "U | 039_file_manager_switch.sh"
    "U | 040_swaync_dgpu_fix.sh --disable"
#    "S | 041_asusd_service_fix.sh"
#    "S | 042_ftp_arch.sh"
    "U | 043_tldr_update.sh"
#    "U | 044_spotify.sh"
    "U | 045_mouse_button_reverse.sh --right"
    "U | 046_neovim_clean.sh"
    "U | 047_neovim_lazy_sync.sh"
    "U | 048_dusk_clipboard_errands_delete.sh --delete"
#    "S | 049_tty_autologin.sh"
    "S | 050_system_services.sh"
#    "S | 051_initramfs_optimization.sh"
#    "U | 052_git_config.sh"
#    "U | 053_new_github_repo_to_backup.sh"
#    "U | 054_reconnect_and_push_new_changes_to_github.sh"
#    "S | 055_grub_optimization.sh"
#    "S | 056_systemdboot_optimization.sh"
#    "S | 057_hosts_files_block.sh"
    "S | 058_gtk_root_symlink.sh"
#    "S | 059_preload_config.sh"
#    "U | 060_kokoro_cpu.sh"
#    "U | 061_faster_whisper_cpu.sh"
    "S | 062_dns_systemd_resolve.sh"
#    "U | 063_hyprexpo_plugin.sh"
    "U | 064_obsidian_pensive_vault_configure.sh"
    "U | 065_cache_purge.sh"
    "S | 066_arch_install_scripts_cleanup.sh"
    "U | 067_cursor_theme_bibata_classic_modern.sh"
    "S | 068_nvidia_open_source.sh"
#    "S | 069_waydroid_setup.sh"
    "U | 070_reverting_sleep_timeout.sh"
    "U | 071_clipboard_persistance.sh"
    "S | 072_intel_media_sdk_check.sh"
    "U | 073_desktop_apps_username_setter.sh"
    "U | 074_firefox_matugen_pywalfox.sh"
#    "U | 075_spicetify_matugen_setup.sh"
    "U | 076_waybar_swap_config.sh"
    "U | 077_mpv_setup.sh"
#    "U | 078_kokoro_gpu_setup.sh" #requires nvidia gpu with at least 4gb vram
#    "U | 079_parakeet_gpu_setup.sh" #requires nvidia gpu with at least 4gb vram
#    "S | 080_btrfs_zstd_compression_stats.sh"
    "U | 081_key_sound_wayclick_setup.sh"
    "U | 082_config_bat_notify.sh --default"
    "U | 083_set_thunar_terminal_kitty.sh"
    "U | 084_package_removal.sh --auto"


    "U | 087_hypr_custom_config_setup.sh"
    "U | 088_hyprctl_reload.sh"
    "U | 980_vesktop_discord_matugen_theme.sh"
    "U | 990_vscode_dms_theme_setup.sh"
)

# ==============================================================================
#  INTERNAL ENGINE (Do not edit below unless you know Bash)
# ==============================================================================

# 1. Safety First
set -o errexit
set -o nounset
set -o pipefail

# 2. Hardcoded Paths
# We use curly braces ${HOME} to safely expand the variable.
readonly SCRIPT_DIR="${HOME}/user_scripts/arch_setup_scripts/scripts"
readonly STATE_FILE="${HOME}/Documents/.install_state"
readonly LOG_FILE="${HOME}/Documents/logs/install_$(date +%Y%m%d_%H%M%S).log"

# Constants
readonly SUDO_REFRESH_INTERVAL=50  # Seconds between sudo refreshes

# Global Variables
declare -g SUDO_PID=""

# 3. Colors
declare -g RED="" GREEN="" BLUE="" YELLOW="" BOLD="" RESET=""

if [[ -t 1 ]] && command -v tput &>/dev/null; then
    if (( $(tput colors 2>/dev/null || echo 0) >= 8 )); then
        RED=$(tput setaf 1)
        GREEN=$(tput setaf 2)
        YELLOW=$(tput setaf 3)
        BLUE=$(tput setaf 4)
        BOLD=$(tput bold)
        RESET=$(tput sgr0)
    fi
fi

# 4. Advanced Logging
setup_logging() {
    # Check if the hardcoded path actually exists
    if [[ ! -d "$SCRIPT_DIR" ]]; then
        echo "CRITICAL ERROR: The hardcoded path does not exist:"
        echo " -> $SCRIPT_DIR"
        exit 1
    fi

    # FIX: Ensure log directory exists to prevent crash on fresh install
    local log_dir
    log_dir="$(dirname "$LOG_FILE")"
    if [[ ! -d "$log_dir" ]]; then
        mkdir -p "$log_dir" || { echo "CRITICAL ERROR: Could not create log directory $log_dir"; exit 1; }
    fi

    touch "$LOG_FILE"
    exec 3>&1 4>&2
    
    # We redirect output to tee. 
    # tee prints to STDOUT (screen) keeping colors.
    # tee pipes to sed (file) stripping colors (ANSI sequences) so the log is clean.
    exec > >(tee >(sed 's/\x1B\[[0-9;]*[a-zA-Z]//g; s/\x1B(B//g' >> "$LOG_FILE")) 2>&1
    
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

# 5. Sudo Management
init_sudo() {
    log "INFO" "Sudo privileges required. Please authenticate."
    if ! sudo -v; then
        log "ERROR" "Sudo authentication failed."
        exit 1
    fi

    # FIX: Use named constant instead of magic number
    ( while true; do sudo -n true; sleep "$SUDO_REFRESH_INTERVAL"; kill -0 "$$" || exit; done 2>/dev/null ) &
    SUDO_PID=$!
    disown "$SUDO_PID"
}

cleanup() {
    local exit_code=$?
    if [[ -n "${SUDO_PID:-}" ]]; then
        kill "$SUDO_PID" 2>/dev/null || true
    fi
    
    if [[ $exit_code -eq 0 ]]; then
        log "SUCCESS" "Orchestrator finished successfully."
    else
        log "ERROR" "Orchestrator exited with error code $exit_code."
    fi
}
trap cleanup EXIT

# 6. Utility Functions
trim() {
    local var="$*"
    var="${var#"${var%%[![:space:]]*}"}"
    var="${var%"${var##*[![:space:]]}"}"
    printf '%s' "$var"
}

# [NEW] Helper to get script description from file header
get_script_description() {
    local filename="$1"
    local desc
    # Try to get first comment line after shebang (line 2)
    # Using SCRIPT_DIR explicitly to be safe regardless of current working directory
    desc=$(sed -n '2s/^#[[:space:]]*//p' "${SCRIPT_DIR}/${filename}" 2>/dev/null)
    if [[ -z "$desc" ]]; then
        # Try line 3 if line 2 was empty or not a comment
        desc=$(sed -n '3s/^#[[:space:]]*//p' "${SCRIPT_DIR}/${filename}" 2>/dev/null)
    fi
    printf "%s" "${desc:-No description available}"
}

# [NEW] Pre-flight check to validate all scripts exist before starting
preflight_check() {
    local missing=0
    log "INFO" "Performing pre-flight validation..."
    
    for entry in "${INSTALL_SEQUENCE[@]}"; do
        local rest="${entry#*|}"
        rest=$(trim "$rest")
        local filename args
        read -r filename args <<< "$rest"
        
        if [[ ! -f "${SCRIPT_DIR}/${filename}" ]]; then
            log "ERROR" "Missing file: ${filename}"
            ((++missing))
        fi
    done
    
    if ((missing > 0)); then
        echo -e "${RED}CRITICAL:${RESET} $missing script(s) are missing from $SCRIPT_DIR."
        read -r -p "Continue anyway? [y/N]: " _choice
        if [[ "${_choice,,}" != "y" ]]; then
            log "ERROR" "Aborting execution."
            exit 1
        fi
    else
        log "SUCCESS" "All sequence files verified."
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

Examples:
    $(basename "$0")              # Normal run
    $(basename "$0") --dry-run    # Preview what would be executed
    $(basename "$0") --reset      # Reset progress and start over
EOF
    exit 0
}

main() {
    # FIX: Root User Guard
    # If run as root, user scripts will create files owned by root in the user's home, causing breakage.
    if [[ $EUID -eq 0 ]]; then
        echo -e "${RED}CRITICAL ERROR: This script must NOT be run as root!${RESET}"
        echo "The script handles sudo privileges internally for specific steps."
        echo "Please run as a normal user: ./ORCHESTRA.sh"
        exit 1
    fi

    # --- ARGUMENT HANDLING (EARLY - before any prompts or logging) ---
    case "${1:-}" in
        --help|-h)
            show_help
            ;;
        --dry-run|-d)
            echo -e "\n${YELLOW}=== DRY RUN MODE ===${RESET}"
            echo -e "Script directory: ${BOLD}${SCRIPT_DIR}${RESET}"
            echo -e "State file: ${BOLD}${STATE_FILE}${RESET}\n"
            
            if [[ ! -d "$SCRIPT_DIR" ]]; then
                echo -e "${RED}WARNING: Script directory does not exist!${RESET}\n"
            fi
            
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
                [[ "$mode" == "S" ]] && mode_label="SUDO"
                
                local status=""
                
                if [[ ! -f "${SCRIPT_DIR}/${filename}" ]]; then
                    status="${RED}[MISSING]${RESET}"
                    ((++missing_count))
                elif [[ -f "$STATE_FILE" ]] && grep -Fxq "$filename" "$STATE_FILE" 2>/dev/null; then
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
            [[ $missing_count -gt 0 ]] && echo -e "  Missing: ${RED}${missing_count}${RESET}"
            echo ""
            echo "No changes were made."
            exit 0
            ;;
        --reset)
            rm -f "$STATE_FILE"
            echo "State file reset. Starting fresh."
            ;;
    esac

    setup_logging
    
    # [NEW] Pre-flight Validation Check
    preflight_check
    
    # FIX: Start timer
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

    # IMPORTANT: We switch to the hardcoded directory so filenames match
    cd "$SCRIPT_DIR" || { log "ERROR" "Failed to cd to $SCRIPT_DIR"; exit 1; }

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

    for entry in "${INSTALL_SEQUENCE[@]}"; do
        # CRITICAL FIX: Use pre-increment ((++var)) instead of post-increment ((var++))
        # Post-increment on 0 returns 0, which 'set -o errexit' treats as a failure!
        ((++current_index))
        
        local mode="${entry%%|*}"
        local rest="${entry#*|}"
        
        mode=$(trim "$mode")
        rest=$(trim "$rest")
        
        # Separate filename from arguments
        local filename args
        read -r filename args <<< "$rest"
        
        # --- MISSING FILE CHECK LOOP ---
        while [[ ! -f "$filename" ]]; do
            log "ERROR" "Script not found: $filename"
            log "ERROR" "Looked in: $SCRIPT_DIR"
            
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
                    log "INFO" "Stopping execution. Please place the script in the correct location and rerun."
                    exit 1
                    ;;
            esac
        done
        
        if grep -Fxq "$filename" "$STATE_FILE"; then
            # RESTORED: Logging of skipped files
            log "WARN" "[${current_index}/${total_scripts}] Skipping $filename (Already Completed)"
            continue
        fi

        # --- USER CONFIRMATION PROMPT (CONDITIONAL) ---
        if [[ $interactive_mode -eq 1 ]]; then
            # [NEW] Get description for better UX
            local desc
            desc=$(get_script_description "$filename")
            
            echo -e "\n${YELLOW}>>> NEXT SCRIPT [${current_index}/${total_scripts}]:${RESET} $filename ${args:+ $args} ($mode)"
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
            # FIX: Added Progress Counter
            log "RUN" "[${current_index}/${total_scripts}] Executing: $filename $args ($mode)"

            local result=0
            if [[ "$mode" == "S" ]]; then
                sudo bash "$filename" $args || result=$?
            elif [[ "$mode" == "U" ]]; then
                bash "$filename" $args || result=$?
            else
                log "ERROR" "Invalid mode '$mode' in config. Use 'S' or 'U'."
                exit 1
            fi

            if [[ $result -eq 0 ]]; then
                echo "$filename" >> "$STATE_FILE"
                log "SUCCESS" "Finished $filename"
                sleep 1
                break # Success: Break retry loop, move to next script
            else
                log "ERROR" "Failed $filename (Exit Code: $result)."
                
                # --- EXECUTION FAIL PROMPT ---
                echo -e "${YELLOW}Action Required:${RESET} Script execution failed."
                read -r -p "Do you want to [S]kip to next, [R]etry, or [Q]uit? (s/r/q): " _fail_choice
                
                case "${_fail_choice,,}" in
                    s|skip)
                        log "WARN" "Skipping $filename (User Selection). NOT marking as complete."
                        SKIPPED_OR_FAILED+=("$filename")
                        break # Break retry loop, move to next script
                        ;;
                    r|retry)
                        log "INFO" "Retrying $filename..."
                        sleep 1
                        continue # Restart retry loop
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
        echo -e "\nIf there were scripts that failed, you can run them individually from:"
        echo -e "${BOLD}${SCRIPT_DIR}/${RESET}"
        echo -e "${YELLOW}================================================================${RESET}\n"
    fi

    # FIX: Calculate elapsed time
    local end_ts=$SECONDS
    local duration=$((end_ts - start_ts))
    local minutes=$((duration / 60))
    local seconds=$((duration % 60))

    # --- COMPLETION & REBOOT NOTICE ---
    # RESTORED: Original verbose instructions with timer added
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
