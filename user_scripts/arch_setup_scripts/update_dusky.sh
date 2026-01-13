#!/usr/bin/env bash
# ==============================================================================
#  ARCH LINUX UPDATE ORCHESTRATOR (v4.3 - Interactive Recovery)
#  Description: Manages dotfile/system updates while preserving user tweaks.
#  Target:      Arch Linux / Hyprland / UWSM / Bash 5.0+
#  Repo Type:   Git Bare Repository (--git-dir=~/dusky --work-tree=~)
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. ENVIRONMENT VALIDATION & STRICT MODE
# ------------------------------------------------------------------------------
set -euo pipefail

if ((BASH_VERSINFO[0] < 5)); then
    printf 'Error: Bash 5.0+ required (found %s)\n' "$BASH_VERSION" >&2
    exit 1
fi

# ------------------------------------------------------------------------------
# 2. CONFIGURATION
# ------------------------------------------------------------------------------
declare -r DOTFILES_GIT_DIR="${HOME}/dusky"
declare -r WORK_TREE="${HOME}"
declare -r SCRIPT_DIR="${HOME}/user_scripts/arch_setup_scripts/scripts"
declare -r REPO_URL="https://github.com/dusklinux/dusky"
declare -r BRANCH="main"
declare -r LOCK_FILE="/tmp/arch-orchestrator.lock"

declare    SUDO_PID=""
declare    STASH_REF=""
declare -a GIT_CMD=()
declare -a FAILED_SCRIPTS=()

# ------------------------------------------------------------------------------
# 3. TERMINAL COLORS
# ------------------------------------------------------------------------------
if [[ -t 1 ]]; then
    declare -r CLR_RED=$'\e[1;31m'
    declare -r CLR_GRN=$'\e[1;32m'
    declare -r CLR_YLW=$'\e[1;33m'
    declare -r CLR_BLU=$'\e[1;34m'
    declare -r CLR_CYN=$'\e[1;36m'
    declare -r CLR_RST=$'\e[0m'
else
    declare -r CLR_RED="" CLR_GRN="" CLR_YLW="" CLR_BLU="" CLR_CYN="" CLR_RST=""
fi

# ------------------------------------------------------------------------------
# 4. THE PLAYLIST
# ------------------------------------------------------------------------------
declare -ra UPDATE_SEQUENCE=(
#    "U | 000_configure_uwsm_gpu.sh"
#    "U | 001_long_sleep_timeout.sh"
#    "S | 002_battery_limiter.sh"
#    "S | 003_pacman_config.sh"
#    "S | 004_pacman_reflector.sh"
#    "S | 005_package_installation.sh"
#    "U | 006_enabling_user_services.sh"
#    "S | 007_openssh_setup.sh"
#    "U | 008_changing_shell_zsh.sh"
#    "S | 009_aur_paru_fallback_yay.sh"
#    "S | 010_warp.sh"
#    "U | 011_paru_packages_optional.sh"
#    "S | 012_battery_limiter_again_dusk.sh"
#    "U | 013_paru_packages.sh"
#    "S | 014_aur_packages_sudo_services.sh"
#    "U | 015_aur_packages_user_services.sh"
#    "S | 016_create_mount_directories.sh"
#    "S | 017_pam_keyring.sh"
    "U | 018_copy_service_files.sh --default"
#    "U | 019_battery_notify_service.sh"
#    "U | 020_fc_cache_fv.sh"
#    "U | 021_matugen_directories.sh"
#    "U | 022_wallpapers_download.sh"
#    "U | 023_blur_shadow_opacity.sh"
#    "U | 024_swww_wallpaper_matugen.sh"
#    "U | 025_qtct_config.sh"
#    "U | 026_waypaper_config_reset.sh"
    "U | 027_animation_symlink.sh"
#    "S | 028_udev_usb_notify.sh"
#    "U | 029_terminal_default.sh"
#    "S | 030_dusk_fstab.sh"
#    "S | 031_firefox_symlink_parition.sh"
#    "S | 032_tlp_config.sh"
#    "S | 033_zram_configuration.sh"
#    "S | 034_zram_optimize_swappiness.sh"
#    "S | 035_powerkey_lid_close_behaviour.sh"
#    "S | 036_logrotate_optimization.sh"
#    "S | 037_faillock_timeout.sh"
#    "U | 038_non_asus_laptop.sh --auto"
#    "U | 039_file_manager_switch.sh"
#    "U | 040_swaync_dgpu_fix.sh --disable"
#    "S | 041_asusd_service_fix.sh"
#    "S | 042_ftp_arch.sh"
#    "U | 043_tldr_update.sh"
#    "U | 044_spotify.sh"
    "U | 045_mouse_button_reverse.sh --right"
#    "U | 046_neovim_clean.sh"
#    "U | 047_neovim_lazy_sync.sh"
#    "U | 048_dusk_clipboard_errands_delete.sh --auto"
#    "S | 049_tty_autologin.sh"
#    "S | 050_system_services.sh"
#    "S | 051_initramfs_optimization.sh"
#    "U | 052_git_config.sh"
#    "U | 053_new_github_repo_to_backup.sh"
#    "U | 054_reconnect_and_push_new_changes_to_github.sh"
#    "S | 055_grub_optimization.sh"
#    "S | 056_systemdboot_optimization.sh"
#    "S | 057_hosts_files_block.sh"
#    "S | 058_gtk_root_symlink.sh"
#    "S | 059_preload_config.sh"
#    "U | 060_kokoro_cpu.sh"
#    "U | 061_faster_whisper_cpu.sh"
#    "S | 062_dns_systemd_resolve.sh"
#    "U | 063_hyprexpo_plugin.sh"
#    "U | 064_obsidian_pensive_vault_configure.sh"
#    "U | 065_cache_purge.sh"
#    "S | 066_arch_install_scripts_cleanup.sh"
#    "U | 067_cursor_theme_bibata_classic_modern.sh"
#    "S | 068_nvidia_open_source.sh"
#    "S | 069_waydroid_setup.sh"
#    "U | 070_reverting_sleep_timeout.sh"
#    "U | 071_clipboard_persistance.sh"
#    "S | 072_intel_media_sdk_check.sh"
    "U | 073_desktop_apps_username_setter.sh"
#    "U | 074_firefox_matugen_pywalfox.sh"
#    "U | 075_spicetify_matugen_setup.sh"
#    "U | 076_waybar_swap_config.sh"
#    "U | 077_mpv_setup.sh"
#    "U | 078_kokoro_gpu_setup.sh" #requires nvidia gpu with at least 4gb vram
#    "U | 079_parakeet_gpu_setup.sh" #requires nvidia gpu with at least 4gb vram
#    "S | 080_btrfs_zstd_compression_stats.sh"
#    "U | 081_key_sound_wayclick_setup.sh"
#    "U | 082_config_bat_notify.sh --default"
    "U | 083_set_thunar_terminal_kitty.sh"
    "U | 084_package_removal.sh --auto"
)

# ==============================================================================
#  CORE ENGINE
# ==============================================================================

log() {
    local -r level="$1" msg="$2"
    case "$level" in
        INFO)    printf '%s[INFO]%s   %s\n' "$CLR_BLU" "$CLR_RST" "$msg" ;;
        OK)      printf '%s[OK]%s     %s\n' "$CLR_GRN" "$CLR_RST" "$msg" ;;
        WARN)    printf '%s[WARN]%s   %s\n' "$CLR_YLW" "$CLR_RST" "$msg" ;;
        ERROR)   printf '%s[ERROR]%s  %s\n' "$CLR_RED" "$CLR_RST" "$msg" >&2 ;;
        SECTION) printf '\n%s═══════ %s %s\n' "$CLR_CYN" "$msg" "$CLR_RST" ;;
    esac
}

trim() {
    local str="$1"
    str="${str#"${str%%[![:space:]]*}"}"
    str="${str%"${str##*[![:space:]]}"}"
    printf '%s' "$str"
}

cleanup() {
    local -r exit_code=$?

    if [[ -n "${SUDO_PID:-}" ]]; then
        kill "$SUDO_PID" 2>/dev/null || true
        wait "$SUDO_PID" 2>/dev/null || true
    fi

    # Only attempt stash recovery if we have an outstanding stash
    if [[ -n "${STASH_REF:-}" && ${#GIT_CMD[@]} -gt 0 ]]; then
        echo
        log WARN "Interrupted with stashed changes!"
        log WARN "Attempting automatic recovery..."

        if "${GIT_CMD[@]}" stash pop --quiet 2>/dev/null; then
            log OK "Your local modifications have been restored."
        else
            log ERROR "Automatic recovery failed."
            log ERROR "Your changes are safely stored. Recover manually with:"
            printf '    %sgit --git-dir="%s" --work-tree="%s" stash pop%s\n' \
                   "$CLR_YLW" "$DOTFILES_GIT_DIR" "$WORK_TREE" "$CLR_RST"
        fi
    fi

    exec 9>&- 2>/dev/null || true
    rm -f "$LOCK_FILE" 2>/dev/null || true

    echo
    if ((${#FAILED_SCRIPTS[@]} > 0)); then
        log WARN "Completed with ${#FAILED_SCRIPTS[@]} failure(s):"
        local script
        for script in "${FAILED_SCRIPTS[@]}"; do
            printf '    %s•%s %s\n' "$CLR_RED" "$CLR_RST" "$script"
        done
    else
        log OK "Orchestration complete. System is synchronized."
    fi

    return "$exit_code"
}

trap cleanup EXIT

init_sudo() {
    log INFO "Acquiring sudo privileges..."

    if ! sudo -v; then
        log ERROR "Sudo authentication failed."
        exit 1
    fi

    (
        while kill -0 "$$" 2>/dev/null; do
            sleep 55
            sudo -n true 2>/dev/null || break
        done
    ) &
    SUDO_PID=$!
    disown "$SUDO_PID"
}

pull_updates() {
    log SECTION "Synchronizing Dotfiles Repository"

    if [[ ! -d "$DOTFILES_GIT_DIR" ]]; then
        log ERROR "Dotfiles bare repository not found: $DOTFILES_GIT_DIR"
        return 1
    fi

    if [[ ! -f "${DOTFILES_GIT_DIR}/HEAD" ]]; then
        log ERROR "Invalid bare repository: ${DOTFILES_GIT_DIR}/HEAD missing"
        return 1
    fi

    GIT_CMD=( /usr/bin/git --git-dir="$DOTFILES_GIT_DIR" --work-tree="$WORK_TREE" )

    log INFO "Checking for local modifications..."

    if ! "${GIT_CMD[@]}" diff-index --quiet HEAD -- 2>/dev/null; then
        log WARN "Uncommitted changes detected in tracked files."
        log INFO "Stashing your changes for safe pull..."

        local stash_msg="orchestrator-auto-$(date +%Y%m%d-%H%M%S)"

        # --- RECOVERY MENU ---
        if ! "${GIT_CMD[@]}" stash push -m "$stash_msg"; then
            log ERROR "Git stash failed. This usually indicates a corrupted git index (needs merge)."
            echo
            printf "%s[ACTION REQUIRED]%s Select a recovery method:\n" "$CLR_YLW" "$CLR_RST"
            echo "  1) Abort (Safe default - stop update)"
            echo "  2) Fix Index (Runs 'git reset' - keeps local changes, fixes errors)"
            echo "  3) Discard Local Changes (Runs 'git reset --hard' - WARN: data loss)"
            echo
            
            # CRITICAL FIX: Ensure failure to read (non-interactive) doesn't crash script in set -e mode
            read -r -p "Enter choice [1-3]: " choice || choice=""
            
            case "$choice" in
                2)
                    log INFO "Resetting git index (preserving local files)..."
                    if "${GIT_CMD[@]}" reset; then
                        log OK "Index reset. Retrying stash..."
                        if ! "${GIT_CMD[@]}" stash push -m "$stash_msg"; then
                             log ERROR "Stash failed again even after reset. Aborting."
                             return 1
                        fi
                    else
                        log ERROR "Git reset failed."
                        return 1
                    fi
                    ;;
                3)
                    log WARN "Hard resetting repository to HEAD (Discarding ALL changes)..."
                    if "${GIT_CMD[@]}" reset --hard HEAD; then
                        log OK "Repository forcefully cleaned. Proceeding."
                    else
                         log ERROR "Git hard reset failed."
                         return 1
                    fi
                    ;;
                *)
                    log ERROR "Aborting by user request or default."
                    return 1
                    ;;
            esac
        fi
        # --- END RECOVERY MENU ---

        # If we successfully stashed (either first try or after Option 2), set the ref
        if [[ -z "${STASH_REF:-}" ]]; then
             if "${GIT_CMD[@]}" stash list | grep -q "$stash_msg"; then
                 STASH_REF="$stash_msg"
                 log OK "Changes stashed: $stash_msg"
             fi
        fi
    fi

    log INFO "Pulling updates from $REPO_URL ($BRANCH)..."

    # Fix: Removed 2>&1 noise
    if ! "${GIT_CMD[@]}" pull --rebase origin "$BRANCH"; then
        log WARN "Pull failed, attempting fetch from URL directly..."

        if ! "${GIT_CMD[@]}" fetch "$REPO_URL" "$BRANCH"; then
            log ERROR "Network error or repository unreachable."
            if [[ -n "${STASH_REF:-}" ]]; then
                "${GIT_CMD[@]}" stash pop --quiet 2>/dev/null && STASH_REF=""
            fi
            return 1
        fi

        if ! "${GIT_CMD[@]}" rebase FETCH_HEAD; then
            log ERROR "Rebase failed. You may have merge conflicts."
            log ERROR "Resolve with: git --git-dir=$DOTFILES_GIT_DIR --work-tree=$WORK_TREE status"
            
            if [[ -n "${STASH_REF:-}" ]]; then
                log WARN "Your local changes remain stashed as: $STASH_REF"
                log WARN "After resolving conflicts, recover with: git stash pop"
                STASH_REF=""  
            fi
            return 1
        fi
    fi

    log OK "Repository updated successfully."

    if [[ -n "${STASH_REF:-}" ]]; then
        log INFO "Restoring your local modifications..."

        if "${GIT_CMD[@]}" stash pop; then
            STASH_REF=""
            log OK "Your customizations have been re-applied."
        else
            log WARN "Merge conflict during stash pop!"
            log WARN "Your changes are preserved in the stash list."
            log WARN "Resolve conflicts, then: git --git-dir=$DOTFILES_GIT_DIR --work-tree=$WORK_TREE stash drop"
            STASH_REF=""
        fi
    fi

    return 0
}

run_script() {
    local -r mode="$1"
    local -r script="$2"
    shift 2
    local -a args=("$@")

    local -r script_path="${SCRIPT_DIR}/${script}"

    if [[ ! -f "$script_path" ]]; then
        log WARN "Script not found: $script"
        return 0
    fi

    if [[ ! -r "$script_path" ]]; then
        log WARN "Script not readable: $script"
        return 0
    fi

    if ((${#args[@]} > 0)); then
        printf '%s→%s %s %s\n' "$CLR_BLU" "$CLR_RST" "$script" "${args[*]}"
    else
        printf '%s→%s %s\n' "$CLR_BLU" "$CLR_RST" "$script"
    fi

    local rc=0
    case "$mode" in
        S)  sudo bash "$script_path" "${args[@]}" || rc=$? ;;
        U)  bash "$script_path" "${args[@]}" || rc=$? ;;
        *)
            log WARN "Unknown mode '$mode' for $script (expected S or U)"
            return 0
            ;;
    esac

    if ((rc != 0)); then
        log ERROR "$script exited with code $rc"
        FAILED_SCRIPTS+=("$script")
    fi

    return 0
}

main() {
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        log ERROR "Another instance is already running (lock: $LOCK_FILE)"
        exit 1
    fi

    init_sudo

    if ! pull_updates; then
        log WARN "Repository sync encountered errors. Continuing with local scripts..."
    fi

    if [[ ! -d "$SCRIPT_DIR" ]]; then
        log ERROR "Script directory missing: $SCRIPT_DIR"
        exit 1
    fi

    log SECTION "Executing Update Sequence"

    local entry mode script_part script
    local -a parts args

    for entry in "${UPDATE_SEQUENCE[@]}"; do
        [[ "$entry" =~ ^[[:space:]]*# ]] && continue
        # Fix: Handle tabs in whitespace check
        [[ -z "${entry//[[:space:]]/}" ]] && continue

        mode=$(trim "${entry%%|*}")
        script_part=$(trim "${entry#*|}")

        IFS=' ' read -ra parts <<< "$script_part"

        script="${parts[0]:-}"
        args=("${parts[@]:1}")

        if [[ -z "$script" ]]; then
            log WARN "Malformed playlist entry: $entry"
            continue
        fi

        run_script "$mode" "$script" "${args[@]}"
    done
}

main "$@"
