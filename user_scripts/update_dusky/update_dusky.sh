#!/usr/bin/env bash
# ==============================================================================
#  DUSKY UPDATER (v8.0) — ARCHITECTURE & USAGE MANUAL
# ==============================================================================
#  Description: Advanced dotfile/system updater for Arch/Hyprland ecosystems.
#               Executes a strict sequence of bash scripts with privilege 
#               separation, atomic backups, and git-bare-repo reconciliation.
#
#  HOW TO USE THIS ENGINE:
#  You only need to care about TWO arrays to configure this updater:
#    1. CUSTOM_SCRIPT_PATHS (Tells the engine WHERE a script lives)
#    2. UPDATE_SEQUENCE     (Tells the engine WHEN and HOW to run a script)
#
# ==============================================================================
#  1. THE CUSTOM_SCRIPT_PATHS ARRAY (The "Where")
# ==============================================================================
#  By default, the engine assumes ALL scripts live in:
#  ~/user_scripts/arch_setup_scripts/scripts/
#
#  If you have a script living SOMEWHERE ELSE in your home directory, you MUST
#  map its exact location here. 
#
#  Syntax:  ["script_filename.sh"]="relative/path/from/home/script_filename.sh"
#
#  ⚠️ CRITICAL RULE: Adding a script here DOES NOT run it! It only acts as a 
#  dictionary lookup for the engine. To actually run it, you must ALSO add it 
#  to the UPDATE_SEQUENCE array below.
#
# ==============================================================================
#  2. THE UPDATE_SEQUENCE ARRAY (The "When & How")
# ==============================================================================
#  This is the execution queue. The engine reads it strictly top-to-bottom.
#  Every entry is divided by pipe characters ('|'). You can use 2 or 3 fields.
#
#  FORMAT A (2 Fields):  "MODE | SCRIPT_NAME ARG1 ARG2"
#  FORMAT B (3 Fields):  "MODE | FLAGS | SCRIPT_NAME ARG1 ARG2"
#
#  --- FIELD 1: MODE ---
#  Determines privilege level.
#    'U' = User mode (Runs normally).
#    'S' = Sudo mode (Prompts for password once, keeps sudo alive in background).
#
#  --- FIELD 2: FLAGS (Optional) ---
#  Controls error handling. 
#  By default, if a script fails (exit code != 0), the ENTIRE updater aborts.
#    'ignore-fail' (or 'true', or 'ignore') = If this specific script fails, 
#                                             log a warning but CONTINUE updating.
#  *Note: If you have no flags, you can leave the field out entirely, or leave 
#   it blank like this: "U | | script.sh"
#
#  --- FIELD 3: COMMAND & THE STRICT ARGUMENT RULE ---
#  This engine has a custom security parser. It deliberately HARD-BLOCKS quotes 
#  (', ") and backslash escapes (\). 
#
#  Because quotes are banned, SPACES ARE ABSOLUTE DELIMITERS. Every space 
#  creates a new $1, $2, $3 argument passed to your script. 
#  Therefore: You CANNOT pass an argument that contains spaces!
#
#  ✅ VALID EXAMPLES:
#      "U | script.sh"                  -> Subscript gets no arguments
#      "U | script.sh --run now"        -> Subscript gets $1="--run", $2="now"
#      "U | script.sh --mode=fast"      -> Subscript gets $1="--mode=fast"
#
#  ❌ INVALID EXAMPLES (WILL CAUSE FATAL ENGINE ERROR):
#      "U | script.sh --msg 'hi there'" -> ERROR: Quotes are forbidden!
#      "U | script.sh --msg hi\ there"  -> ERROR: Backslashes are forbidden!
#
# ==============================================================================
#  CHEAT SHEET / COPY-PASTE EXAMPLES
# ==============================================================================
#
#  1. Standard user script:
#     "U | 015_set_thunar_terminal.sh"
#
#  2. Sudo script (will halt the whole updater if it fails):
#     "S | 060_package_installation.sh"
#
#  3. Sudo script passing arguments (--auto and --force):
#     "S | 050_pacman_config.sh --auto --force"
#
#  4. User script that is ALLOWED TO FAIL (won't stop the updater):
#     "U | ignore-fail | 150_wallpapers_download.sh --quiet"
#
#  5. Sudo script allowed to fail:
#     "S | ignore-fail | 085_warp.sh --connect"
#
#  6. Legacy format (still supported by the parser):
#     "U | true 150_wallpapers_download.sh"
# ==============================================================================
set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true
shopt -s extglob 2>/dev/null || true

if (( BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 3) )); then
    printf 'Error: Bash 5.3+ required (found %s)\n' "$BASH_VERSION" >&2
    exit 1
fi

# ==============================================================================
# CONSTANTS
# ==============================================================================
declare -ri SUDO_KEEPALIVE_INTERVAL=55
declare -ri FETCH_TIMEOUT=60
declare -ri CLONE_TIMEOUT=120
declare -ri FETCH_MAX_ATTEMPTS=5
declare -ri FETCH_INITIAL_BACKOFF=2
declare -ri PROMPT_TIMEOUT_LONG=60
declare -ri PROMPT_TIMEOUT_SHORT=30
declare -ri LOG_RETENTION_DAYS=14
declare -ri BACKUP_RETENTION_DAYS=14
declare -ri DISK_MIN_FREE_MB=100
declare -ri DISK_COPY_RESERVE_MB=64
declare -r VERSION="8.0.1"
declare -ri SYNC_RC_RECOVERABLE=10
declare -ri SYNC_RC_UNSAFE=20

# ==============================================================================
# CONFIGURATION
# ==============================================================================
declare -r DOTFILES_GIT_DIR="${HOME}/dusky"
declare -r WORK_TREE="${HOME}"
declare -r SCRIPT_DIR="${HOME}/user_scripts/arch_setup_scripts/scripts"
declare -r LOG_BASE_DIR="${HOME}/Documents/logs"
declare -r BACKUP_BASE_DIR="${HOME}/Documents/dusky_backups"
declare -r STATE_HOME_DIR="${XDG_STATE_HOME:-${HOME}/.local/state}/dusky"
declare -r FALLBACK_LOG_BASE_DIR="${STATE_HOME_DIR}/logs"
declare -r FALLBACK_BACKUP_BASE_DIR="${STATE_HOME_DIR}/backups"
declare -r REPO_URL="https://github.com/dusklinux/dusky"
declare -r BRANCH="main"
declare -r UPSTREAM_REMOTE="dusky-upstream"
declare -r UPSTREAM_TRACKING_REF="refs/dusky-updater/upstream/${BRANCH}"

# ==============================================================================
# USER CONFIGURATION
# ==============================================================================

# ------------------------------------------------------------------------------
# CUSTOM SCRIPT PATHS Guide
# ------------------------------------------------------------------------------
# DO NOT REMOVE THESE COMMENTS, THESE ARE INSTRUCTIONS FOR ADDING SCRIPTS WITH CUSTOM PATH
# Map specific scripts to custom paths relative to ${HOME}.
# If a script in UPDATE_SEQUENCE matches a key here, this path is used.
# Format: ["script_name.sh"]="path/from/home/script_name.sh"

# ⚠️ IMPORTANT INSTRUCTIONS:
# 1. DEFINITION ONLY: This array ONLY maps the script name to a custom file location.
#    Adding a script here DOES NOT cause it to run automatically.
#
# 2. EXECUTION REQUIRED: To actually run the script, you MUST also add it to the
#    'UPDATE_SEQUENCE' list further down in this file.
#
# Format: ["script_name.sh"]="path/relative/to/home/script_name.sh"
#
# Example:
#   ["warp_toggle.sh"]="user_scripts/networking/warp_toggle.sh"
#   Then in UPDATE_SEQUENCE add:
#     "S | warp_toggle.sh"
#     "S | ignore-fail | warp_toggle.sh --auto"

# ------------------------------------------------------------------------------
# CUSTOM SCRIPT PATHS
# ------------------------------------------------------------------------------
declare -A CUSTOM_SCRIPT_PATHS=(
    ["warp_toggle.sh"]="user_scripts/networking/warp_toggle.sh"
    ["fix_theme_dir.sh"]="user_scripts/misc_extra/fix_theme_dir.sh"
    ["pacman_packages.sh"]="user_scripts/misc_extra/pacman_packages.sh"
    ["paru_packages.sh"]="user_scripts/misc_extra/paru_packages.sh"
    ["copy_service_files.sh"]="user_scripts/misc_extra/copy_service_files.sh"
    ["update_checker.sh"]="user_scripts/update_dusky/update_checker/update_checker.sh"
    ["cc_restart.sh"]="user_scripts/dusky_system/reload_cc/cc_restart.sh"
    ["dusky_service_manager.sh"]="user_scripts/services/dusky_service_manager.sh"
    ["append_defaults_keybinds_edit_here.sh"]="user_scripts/misc_extra/append_defaults_keybinds_edit_here.sh"
    ["append_sourcing_line_workspace.sh"]="user_scripts/misc_extra/delete_in_3_weeks/append_sourcing_line_workspace.sh"
    ["append_gaps_line_in_appearance.sh"]="user_scripts/misc_extra/delete_in_3_weeks/append_gaps_line_in_appearance.sh"
    ["dusky_commands_before.sh"]="user_scripts/misc_extra/dusky_commands_before.sh"
    ["dusky_commands_after.sh"]="user_scripts/misc_extra/dusky_commands_after.sh"
    ["rofi_wallpaper_selctor.sh"]="user_scripts/rofi/rofi_wallpaper_selctor.sh"
    ["hypr_anim.sh"]="user_scripts/rofi/hypr_anim.sh"
    ["dusky_matugen_config_tui.sh"]="user_scripts/theme_matugen/dusky_matugen_config_tui.sh"
    ["dusky_firefox_tui.sh"]="user_scripts/theme_matugen/dusky_firefox_tui.sh"
    ["theme_ctl.sh"]="user_scripts/theme_matugen/theme_ctl.sh"
    ["update_counter.sh"]="user_scripts/waybar/update_counter.sh"
)

# ------------------------------------------------------------------------------
# UPDATE SEQUENCE
# ------------------------------------------------------------------------------
# Entry formats:
#   "U | script.sh --auto"
#   "S | ignore-fail | script.sh --auto"
#   "U | | script.sh --auto"
#
# Rules:
#   - Field 1: U or S
#   - Field 2: optional flags; currently supported: ignore-fail
#   - Field 3: script name plus whitespace-separated arguments
#   - Legacy form "U | true script.sh --auto" is still accepted
#   - Quotes, backslash escapes, and additional "|" characters inside the
#     command field are not supported
declare -ra UPDATE_SEQUENCE=(

#================= CUSTOM=====================
    "U | dusky_commands_before.sh"
#================= Scripts =====================

    "U | 005_hypr_custom_config_setup.sh"
    "U | 010_package_removal.sh --auto"


#================= CUSTOM=====================
    "S | pacman_packages.sh"
    "U | paru_packages.sh"
    "U | rofi_wallpaper_selctor.sh --cache-only --progress"
#================= Scripts =====================


    "U | 015_set_thunar_terminal_kitty.sh"
    "U | 020_desktop_apps_username_setter.sh --quiet"
#    "U | 025_configure_keyboard.sh"
#    "U | 035_configure_uwsm_gpu.sh --auto"
#    "U | 040_long_sleep_timeout.sh"
#    "S | 045_battery_limiter.sh"
#    "S | 050_pacman_config.sh --auto"
    "S | 051_pacman_hooks.sh --auto"
#    "S | 055_pacman_reflector.sh"
#    "S | 060_package_installation.sh"
#    "U | 065_enabling_user_services.sh"
#    "S | 070_openssh_setup.sh"
#    "U | 075_changing_shell_zsh.sh"
#    "S | 080_aur_paru_fallback_yay.sh"
#    "S | 085_warp.sh"
#    "U | 090_paru_packages_optional.sh"
#    "S | 095_battery_limiter_again_dusk.sh"
#    "U | 100_paru_packages.sh"
#    "S | 110_aur_packages_sudo_services.sh"
#    "U | 115_aur_packages_user_services.sh"
#    "S | 120_create_mount_directories.sh"
#    "S | 125_pam_keyring.sh"
    "U | 130_copy_service_files.sh --default"
    "U | 131_dbus_copy_service_files.sh"
#    "U | 135_battery_notify_service.sh"
#    "U | 140_fc_cache_fv.sh"
#    "U | 145_matugen_directories.sh"
#    "U | 150_wallpapers_download.sh"
#    "U | 155_blur_shadow_opacity.sh"
#    "U | ignore-fail | 160_theme_ctl.sh"
#    "U | 165_qtct_config.sh"
    "U | 170_waypaper_config_reset.sh"
#    "U | 175_animation_default.sh"
#    "S | 180_udev_usb_notify.sh"
#    "U | 185_terminal_default.sh"
#    "S | 190_dusk_fstab.sh"
#    "S | 195_firefox_symlink_parition.sh"
#    "S | 200_tlp_config.sh"
#    "S | 205_zram_configuration.sh"
#    "S | 210_zram_optimize_swappiness.sh"
#    "S | 215_powerkey_lid_close_behaviour.sh"
#    "S | 220_logrotate_optimization.sh"
#    "S | 225_faillock_timeout.sh"
    "U | 230_non_asus_laptop.sh --auto"
    "U | 235_file_manager_switch.sh --apply-state"
    "U | 236_browser_switcher.sh --apply-state"
    "U | 237_text_editer_switcher.sh --apply-state"
    "U | 238_terminal_switcher.sh --apply-state"
#    "U | 240_swaync_dgpu_fix.sh --disable"
#    "S | 245_asusd_service_fix.sh"
#    "S | 250_ftp_arch.sh"
#    "U | 255_tldr_update.sh"
#    "U | 260_spotify.sh"
#    "U | 265_mouse_button_reverse.sh --right"
#    "U | 280_dusk_clipboard_errands_delete.sh --delete"
#    "S | 285_tty_autologin.sh"
#    "S | 290_system_services.sh"
#    "S | 295_initramfs_optimization.sh"
#    "U | 300_git_config.sh"
#    "U | 305_new_github_repo_to_backup.sh"
#    "U | 310_reconnect_and_push_new_changes_to_github.sh"
#    "S | 315_grub_optimization.sh"
#    "S | 320_systemdboot_optimization.sh"
#    "S | 325_hosts_files_block.sh"
#    "S | 330_gtk_root_symlink.sh"
#    "S | 335_preload_config.sh"
#    "U | 340_kokoro_cpu.sh"
#    "U | 345_faster_whisper_cpu.sh"
#    "S | 350_dns_systemd_resolve.sh"
#    "U | 355_hyprexpo_plugin.sh"
#    "U | 356_dusky_plugin_manager.sh"
#    "U | 360_obsidian_pensive_vault_configure.sh"
#    "U | 365_cache_purge.sh"
#    "S | 370_arch_install_scripts_cleanup.sh"
#    "U | 375_cursor_theme_bibata_classic_modern.sh"
#    "S | 380_nvidia_open_source.sh"
#    "S | 385_waydroid_setup.sh"
#    "U | 390_clipboard_persistance.sh"
#    "S | 395_intel_media_sdk_check.sh"
#    "U | 400_firefox_matugen_pywalfox.sh"
#    "U | 405_spicetify_matugen_setup.sh"
#    "U | 410_waybar_swap_config.sh"
#    "U | 415_mpv_setup.sh"
#    "U | 420_kokoro_gpu_setup.sh"
#    "U | 425_parakeet_gpu_setup.sh"
#    "S | 430_btrfs_zstd_compression_stats.sh"
    "U | 434_wayclick_soundpacks_download.sh --auto"
#    "U | 435_key_sound_wayclick_setup.sh --setup"
#    "U | 440_config_bat_notify.sh --default"
#    "U | 450_generate_colorfiles_for_current_wallpaer.sh"
    "U | 455_hyprctl_reload.sh"
    "U | 460_switch_clipboard.sh --terminal --force"
#    "S | 465_sddm_setup.sh"
#    "U | 470_vesktop_matugen.sh"
#    "U | 475_reverting_sleep_timeout.sh"
#    "U | 480_dusky_commands.sh"
    "S | 485_sudoers_nopassword.sh"

#================= CUSTOM=====================

    "U | copy_service_files.sh --default"
    "U | update_checker.sh --num"
    "U | cc_restart.sh --quiet"
    "S | dusky_service_manager.sh"
    "U | append_defaults_keybinds_edit_here.sh"
    "U | append_sourcing_line_workspace.sh"
    "U | append_gaps_line_in_appearance.sh"
    "U | ignore-fail | dusky_matugen_config_tui.sh --smart"
#    "U | ignore-fail | dusky_firefox_tui.sh --sync --all"
    "U | ignore-fail | hypr_anim.sh --current"
    "U | ignore-fail | theme_ctl.sh refresh"
    "U | ignore-fail | update_counter.sh"
    "U | dusky_commands_after.sh"
)

# ==============================================================================
# BINARIES / STATIC RUNTIME
# ==============================================================================
declare -g GIT_BIN="" BASH_BIN=""
GIT_BIN="$(command -v git 2>/dev/null || true)"
BASH_BIN="$(command -v bash 2>/dev/null || true)"

if [[ -z "$GIT_BIN" || ! -x "$GIT_BIN" ]]; then
    printf 'Error: git not found\n' >&2
    exit 1
fi
if [[ -z "$BASH_BIN" || ! -x "$BASH_BIN" ]]; then
    printf 'Error: bash not found\n' >&2
    exit 1
fi
readonly GIT_BIN BASH_BIN

declare -gr MAIN_PID=$$
declare -gr RUN_TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
declare -gr SELF_PATH="$(realpath -- "$0" 2>/dev/null || readlink -f -- "$0" 2>/dev/null || printf '%s' "$0")"
declare -gr CACHED_USER="${USER:-$(id -un 2>/dev/null || printf '%s' unknown)}"

declare -ga ORIGINAL_ARGS=("$@")
declare -ga GIT_CMD=("$GIT_BIN" --git-dir="$DOTFILES_GIT_DIR" --work-tree="$WORK_TREE")

# ==============================================================================
# MUTABLE RUNTIME STATE
# ==============================================================================
declare -g RUNTIME_DIR=""
declare -g ACTIVE_LOG_BASE_DIR=""
declare -g ACTIVE_BACKUP_BASE_DIR=""
declare -g LOG_FILE=""
declare -g LOCK_FILE=""
declare -g LOCK_FD=""
declare -g SUDO_PID=""
declare -g CURRENT_PHASE="startup"
declare -g SUMMARY_PRINTED=false
declare -g SKIP_FINAL_SUMMARY=false
declare -g SYNC_FAILED=false

declare -g USER_MODS_BACKUP_DIR=""
declare -g FULL_TRACKED_BACKUP_DIR=""
declare -g GIT_HISTORY_BACKUP_DIR=""
declare -g MERGE_DIR=""
declare -g PRE_SYNC_HEAD=""

declare -ga CREATED_TEMP_DIRS=()
declare -ga COLLISION_BACKUP_DIRS=()
declare -gA COLLISION_MOVED_PATHS=()
declare -ga HARD_FAILED_SCRIPTS=()
declare -ga SOFT_FAILED_SCRIPTS=()
declare -ga SKIPPED_SCRIPTS=()

declare -ga CHANGE_PATHS=()
declare -gA CHANGE_STATUS=()
declare -gA CHANGE_OLD_MODE=()
declare -gA CHANGE_OLD_OID=()
declare -gA CHANGE_BACKUP_HAS_FILE=()

declare -ga MANIFEST_MODE=()
declare -ga MANIFEST_SCRIPT=()
declare -ga MANIFEST_IGNORE_FAIL=()
declare -ga MANIFEST_ARGV_NAME=()
declare -ga MANIFEST_PATH=()
declare -ga MANIFEST_PATH_STATE=()
declare -ga MANIFEST_IS_CUSTOM=()

declare -g OPT_DRY_RUN=false
declare -g OPT_SKIP_SYNC=false
declare -g OPT_SYNC_ONLY=false
declare -g OPT_FORCE=false
declare -g OPT_STOP_ON_FAIL=false
declare -g OPT_POST_SELF_UPDATE=false
declare -g OPT_ALLOW_DIVERGED_RESET=false
declare -g OPT_NEEDS_SUDO=false

# ==============================================================================
# COLORS
# ==============================================================================
if [[ -t 1 ]]; then
    declare -r CLR_RED=$'\e[1;31m'
    declare -r CLR_GRN=$'\e[1;32m'
    declare -r CLR_YLW=$'\e[1;33m'
    declare -r CLR_BLU=$'\e[1;34m'
    declare -r CLR_CYN=$'\e[1;36m'
    declare -r CLR_RST=$'\e[0m'
else
    declare -r CLR_RED=""
    declare -r CLR_GRN=""
    declare -r CLR_YLW=""
    declare -r CLR_BLU=""
    declare -r CLR_CYN=""
    declare -r CLR_RST=""
fi

# ==============================================================================
# BASIC HELPERS
# ==============================================================================
trim() {
    local s="${1-}"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

ensure_not_running_as_root() {
    if (( EUID == 0 )); then
        printf 'Error: Do not run this updater as root.\n' >&2
        printf 'Run it as your normal user; the script will use sudo only for "S" entries.\n' >&2
        exit 1
    fi
}

split_manifest_fields() {
    local entry="${1-}"
    local -n out_ref="$2"
    local -a raw_fields=()
    local field=""

    IFS='|' read -r -a raw_fields <<< "$entry"
    out_ref=()

    for field in "${raw_fields[@]}"; do
        out_ref+=("$(trim "$field")")
    done
}

path_exists() {
    [[ -e "$1" || -L "$1" ]]
}

path_parent() {
    local p="${1:-.}"

    case "$p" in
        /)
            printf '/'
            ;;
        */*)
            p="${p%/*}"
            printf '%s' "${p:-/}"
            ;;
        *)
            printf '.'
            ;;
    esac
}

path_base() {
    printf '%s' "${1##*/}"
}

nearest_existing_ancestor() {
    local p="${1:-.}"

    [[ -n "$p" ]] || p='.'

    while [[ ! -e "$p" && ! -L "$p" ]]; do
        case "$p" in
            /|.)
                break
                ;;
            */*)
                p="${p%/*}"
                [[ -n "$p" ]] || p='/'
                ;;
            *)
                p='.'
                ;;
        esac
    done

    printf '%s' "$p"
}

path_device_id() {
    local dev=""

    dev="$(stat -c '%d' -- "$1" 2>/dev/null || true)"
    [[ "$dev" =~ ^[0-9]+$ ]] || return 1
    printf '%s' "$dev"
}

quote_for_log() {
    printf '%q' "$1"
}

join_quoted_argv() {
    local out="" arg="" quoted=""

    for arg in "$@"; do
        printf -v quoted '%q' "$arg"
        out+="${quoted} "
    done

    printf '%s' "${out% }"
}

strip_ansi() {
    REPLY="${1//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
}

log() {
    (($# >= 2)) || return 1

    local -r level="$1"
    local -r msg="$2"
    local timestamp="" prefix=""

    printf -v timestamp '%(%H:%M:%S)T' -1

    case "$level" in
        INFO)    prefix="${CLR_BLU}[INFO ]${CLR_RST}" ;;
        OK)      prefix="${CLR_GRN}[OK   ]${CLR_RST}" ;;
        WARN)    prefix="${CLR_YLW}[WARN ]${CLR_RST}" ;;
        ERROR)   prefix="${CLR_RED}[ERROR]${CLR_RST}" ;;
        SECTION) prefix=$'\n'"${CLR_CYN}═══════${CLR_RST}" ;;
        RAW)     prefix="" ;;
        *)       prefix="[$level]" ;;
    esac

    if [[ "$level" == "RAW" ]]; then
        printf '%s\n' "$msg"
    elif [[ "$level" == "SECTION" ]]; then
        printf '%s %s\n' "$prefix" "$msg"
    else
        printf '%s %s\n' "$prefix" "$msg"
    fi

    if [[ -n "$LOG_FILE" && -w "$LOG_FILE" ]]; then
        strip_ansi "$msg"
        printf '[%s] [%-7s] %s\n' "$timestamp" "$level" "$REPLY" >> "$LOG_FILE"
    fi
}

desktop_notify() {
    [[ "$OPT_DRY_RUN" == true ]] && return 0

    local urgency="${1:-normal}"
    local summary="${2:-Dusky Update}"
    local body="${3:-}"

    if command -v notify-send &>/dev/null; then
        timeout 3 notify-send --urgency="$urgency" --app-name="Dusky Updater" "$summary" "$body" \
            >/dev/null 2>&1 || true
    fi
}

show_help() {
    cat <<'HELPEOF'
Dusky Updater — Dotfile sync and setup tool for Arch Linux / Hyprland

Usage: dusky_updater.sh [OPTIONS]

Options:
  --help, -h               Show this help message and exit
  --version                Show version and exit
  --dry-run                Preview actions without making changes
  --skip-sync              Skip git sync, only run the script sequence
  --sync-only              Pull updates but do not run scripts
  --force                  Skip confirmation prompts
  --stop-on-fail           Abort script execution on first hard failure
  --allow-diverged-reset   In non-interactive mode, allow reset on diverged or unrelated history
  --list                   List all active scripts in the update sequence

Update sequence entry formats:
  U | script.sh --auto
  S | ignore-fail | script.sh --auto
  U | | script.sh --auto

Field 1:
  U = run as user
  S = run with sudo

Field 2:
  Optional flags. Supported values:
    ignore-fail

Legacy format is still accepted:
  U | true script.sh --auto

Rules:
  - Arguments are whitespace-separated only
  - Quotes, backslash escapes, and extra "|" characters in the command field are not supported

Logs are saved to:
  ~/Documents/logs/
  Fallback: ~/.local/state/dusky/logs/

Backups are saved to:
  ~/Documents/dusky_backups/
  Fallback: ~/.local/state/dusky/backups/
HELPEOF
}

show_version() {
    printf 'Dusky Updater v%s\n' "$VERSION"
}

require_sudo_if_needed() {
    local i=""
    [[ "$OPT_SYNC_ONLY" == true || "$OPT_DRY_RUN" == true ]] && return 0

    for i in "${!MANIFEST_MODE[@]}"; do
        if [[ "${MANIFEST_MODE[$i]}" == "S" ]]; then
            command -v sudo >/dev/null 2>&1 || {
                log ERROR "sudo is required by UPDATE_SEQUENCE but is not installed or not in PATH"
                return 1
            }
            OPT_NEEDS_SUDO=true
            return 0
        fi
    done

    return 0
}

file_sha256() {
    local line=""
    line="$(sha256sum -- "$1" 2>/dev/null)" || return 1
    printf '%s' "${line%% *}"
}

# ==============================================================================
# MANIFEST PARSING
# ==============================================================================
parse_update_sequence_manifest() {
    local entry="" mode="" flags_part="" command_part="" script=""
    local ignore_fail=false
    local -a fields=()
    local -a parts=()
    local -a flag_tokens=()
    local idx=0
    local flag=""

    MANIFEST_MODE=()
    MANIFEST_SCRIPT=()
    MANIFEST_IGNORE_FAIL=()
    MANIFEST_ARGV_NAME=()
    MANIFEST_PATH=()
    MANIFEST_PATH_STATE=()
    MANIFEST_IS_CUSTOM=()

    for entry in "${UPDATE_SEQUENCE[@]}"; do
        [[ -z "${entry//[[:space:]]/}" ]] && continue
        [[ "$entry" == *'|'* ]] || {
            printf 'Error: Malformed UPDATE_SEQUENCE entry: %s\n' "$entry" >&2
            exit 1
        }

        fields=()
        split_manifest_fields "$entry" fields

        case "${#fields[@]}" in
            2)
                mode="${fields[0]}"
                flags_part=""
                command_part="${fields[1]}"
                ;;
            3)
                mode="${fields[0]}"
                flags_part="${fields[1]}"
                command_part="${fields[2]}"
                ;;
            *)
                printf 'Error: UPDATE_SEQUENCE entry must contain 2 or 3 pipe-separated fields: %s\n' "$entry" >&2
                exit 1
                ;;
        esac

        [[ "$mode" == "U" || "$mode" == "S" ]] || {
            printf 'Error: Invalid mode in UPDATE_SEQUENCE entry: %s\n' "$entry" >&2
            exit 1
        }

        ignore_fail=false
        if [[ -n "$flags_part" ]]; then
            read -r -a flag_tokens <<< "${flags_part//,/ }"
            for flag in "${flag_tokens[@]}"; do
                case "$flag" in
                    true|ignore|ignore-fail)
                        ignore_fail=true
                        ;;
                    "")
                        ;;
                    *)
                        printf 'Error: Unsupported flag in UPDATE_SEQUENCE entry: %s (entry: %s)\n' "$flag" "$entry" >&2
                        exit 1
                        ;;
                esac
            done
        fi

        [[ -n "$command_part" ]] || {
            printf 'Error: Missing script in UPDATE_SEQUENCE entry: %s\n' "$entry" >&2
            exit 1
        }

        case "$command_part" in
            *\'*|*\"*|*\\*)
                printf 'Error: UPDATE_SEQUENCE command field does not support quotes or backslash escapes: %s\n' "$entry" >&2
                exit 1
                ;;
        esac

        parts=()
        read -r -a parts <<< "$command_part"

        ((${#parts[@]} > 0)) || {
            printf 'Error: Missing script in UPDATE_SEQUENCE entry: %s\n' "$entry" >&2
            exit 1
        }

        if [[ "${parts[0]}" == "true" ]]; then
            ignore_fail=true
            parts=("${parts[@]:1}")
            ((${#parts[@]} > 0)) || {
                printf 'Error: Missing script after "true" in UPDATE_SEQUENCE entry: %s\n' "$entry" >&2
                exit 1
            }
        fi

        script="${parts[0]}"
        [[ -n "$script" ]] || {
            printf 'Error: Empty script name in UPDATE_SEQUENCE entry: %s\n' "$entry" >&2
            exit 1
        }

        local argv_name="MANIFEST_ARGV_${idx}"
        declare -ga "$argv_name"
        local -n argv_ref="$argv_name"
        argv_ref=("${parts[@]:1}")

        MANIFEST_MODE+=("$mode")
        MANIFEST_SCRIPT+=("$script")
        MANIFEST_IGNORE_FAIL+=("$ignore_fail")
        MANIFEST_ARGV_NAME+=("$argv_name")
        MANIFEST_PATH+=("")
        MANIFEST_PATH_STATE+=("unknown")
        MANIFEST_IS_CUSTOM+=("false")

        ((idx++)) || true
    done
}

list_active_scripts() {
    ((${#MANIFEST_MODE[@]} > 0)) || parse_update_sequence_manifest

    local i=0 display_mode="" script="" display_args=""
    printf 'Active scripts in update sequence:\n\n'
    for i in "${!MANIFEST_MODE[@]}"; do
        display_mode="${MANIFEST_MODE[$i]}"
        if [[ "${MANIFEST_IGNORE_FAIL[$i]}" == "true" ]]; then
            display_mode="${display_mode},ignore"
        fi
        script="${MANIFEST_SCRIPT[$i]}"
        local -n argv_ref="${MANIFEST_ARGV_NAME[$i]}"
        display_args="$(join_quoted_argv "${argv_ref[@]}")"
        printf '  %3d) [%s] %s' "$((i + 1))" "$display_mode" "$script"
        [[ -n "$display_args" ]] && printf ' %s' "$display_args"
        printf '\n'
    done
    printf '\nTotal: %d active script(s)\n' "${#MANIFEST_MODE[@]}"
}

parse_args() {
    while (($# > 0)); do
        case "$1" in
            --help|-h)
                show_help
                exit 0
                ;;
            --version)
                show_version
                exit 0
                ;;
            --dry-run)
                OPT_DRY_RUN=true
                ;;
            --skip-sync)
                OPT_SKIP_SYNC=true
                ;;
            --sync-only)
                OPT_SYNC_ONLY=true
                ;;
            --force)
                OPT_FORCE=true
                ;;
            --stop-on-fail)
                OPT_STOP_ON_FAIL=true
                ;;
            --allow-diverged-reset)
                OPT_ALLOW_DIVERGED_RESET=true
                ;;
            --list)
                list_active_scripts
                exit 0
                ;;
            --post-self-update)
                OPT_POST_SELF_UPDATE=true
                ;;
            -*)
                printf 'Unknown option: %s\nTry --help for usage information.\n' "$1" >&2
                exit 1
                ;;
            *)
                printf 'Unexpected argument: %s\nTry --help for usage information.\n' "$1" >&2
                exit 1
                ;;
        esac
        shift
    done

    if [[ "$OPT_SKIP_SYNC" == true && "$OPT_SYNC_ONLY" == true ]]; then
        printf 'Error: --skip-sync and --sync-only are mutually exclusive\n' >&2
        exit 1
    fi
}

# ==============================================================================
# SYSTEM / STORAGE HELPERS
# ==============================================================================
check_dependencies() {
    local -a missing=()
    local cmd=""

    for cmd in flock sha256sum timeout mktemp find df du stat tee; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done

    if ((${#missing[@]} > 0)); then
        printf 'Error: Missing required commands: %s\n' "${missing[*]}" >&2
        printf 'Install with: sudo pacman -S bash coreutils findutils git util-linux\n' >&2
        exit 1
    fi
}

ensure_secure_runtime_dir() {
    local dir="$1"

    if [[ -e "$dir" && ! -d "$dir" ]]; then
        return 1
    fi
    if [[ -L "$dir" ]]; then
        return 1
    fi
    if [[ ! -d "$dir" ]]; then
        mkdir -p -- "$dir" || return 1
    fi
    chmod 700 -- "$dir" 2>/dev/null || true

    [[ -d "$dir" && ! -L "$dir" && -O "$dir" && -w "$dir" ]]
}

ensure_storage_dir() {
    local dir="$1"

    if [[ -L "$dir" ]]; then
        return 1
    fi
    if [[ -e "$dir" && ! -d "$dir" ]]; then
        return 1
    fi
    if [[ ! -d "$dir" ]]; then
        mkdir -p -- "$dir" || return 1
    fi
    chmod 700 -- "$dir" 2>/dev/null || true

    [[ -d "$dir" && ! -L "$dir" && -O "$dir" && -w "$dir" ]]
}

choose_storage_dir() {
    local preferred="$1"
    local fallback="$2"
    local -n out_ref="$3"

    if ensure_storage_dir "$preferred"; then
        out_ref="$preferred"
        return 0
    fi

    if ensure_storage_dir "$fallback"; then
        out_ref="$fallback"
        return 0
    fi

    return 1
}

setup_runtime_dir() {
    local candidate=""

    if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
        candidate="${XDG_RUNTIME_DIR}/dusky-updater"
        if ensure_secure_runtime_dir "$candidate"; then
            RUNTIME_DIR="$candidate"
            LOCK_FILE="${candidate}/lock"
            return 0
        fi
    fi

    candidate="/tmp/dusky-updater-${EUID}"
    if ! ensure_secure_runtime_dir "$candidate"; then
        printf 'Error: Cannot create secure runtime directory: %s\n' "$candidate" >&2
        exit 1
    fi

    RUNTIME_DIR="$candidate"
    LOCK_FILE="${candidate}/lock"
}

make_private_dir_under() {
    local base="$1"
    local template="$2"
    local dir=""

    ensure_storage_dir "$base" || return 1
    dir="$(mktemp -d -p "$base" "$template")" || return 1
    chmod 700 -- "$dir" 2>/dev/null || true
    printf '%s' "$dir"
}

make_private_file_under() {
    local base="$1"
    local template="$2"
    local file=""

    ensure_storage_dir "$base" || return 1
    file="$(mktemp -p "$base" "$template")" || return 1
    chmod 600 -- "$file" 2>/dev/null || true
    printf '%s' "$file"
}

setup_storage_roots() {
    choose_storage_dir "$LOG_BASE_DIR" "$FALLBACK_LOG_BASE_DIR" ACTIVE_LOG_BASE_DIR || {
        printf 'Error: Cannot create any usable log directory\n' >&2
        exit 1
    }

    choose_storage_dir "$BACKUP_BASE_DIR" "$FALLBACK_BACKUP_BASE_DIR" ACTIVE_BACKUP_BASE_DIR || {
        printf 'Error: Cannot create any usable backup directory\n' >&2
        exit 1
    }
}

setup_logging() {
    LOG_FILE="$(make_private_file_under "$ACTIVE_LOG_BASE_DIR" "dusky_update_${RUN_TIMESTAMP}_XXXXXX.log")" || {
        printf 'Error: Cannot create log file\n' >&2
        exit 1
    }

    {
        printf '================================================================================\n'
        printf ' DUSKY UPDATE LOG — %s\n' "$RUN_TIMESTAMP"
        printf ' Kernel: %s | User: %s | Bash: %s\n' "$(uname -r)" "$CACHED_USER" "$BASH_VERSION"
        printf '================================================================================\n'
    } >> "$LOG_FILE"
}

acquire_lock() {
    exec {LOCK_FD}>>"$LOCK_FILE" || {
        log ERROR "Cannot open lock file: $LOCK_FILE"
        return 1
    }

    if ! flock -n "$LOCK_FD"; then
        log ERROR "Another instance is already running."
        local lock_real fd pid cmdline summary=""
        local -A seen_pids=()
        lock_real="$(readlink -f -- "$LOCK_FILE" 2>/dev/null || printf '%s' "$LOCK_FILE")"
        for fd in /proc/[0-9]*/fd/*; do
            [[ -e "$fd" ]] || continue
            if [[ "$(readlink -f -- "$fd" 2>/dev/null || true)" == "$lock_real" ]]; then
                pid="${fd#/proc/}"
                pid="${pid%%/*}"
                [[ "$pid" == "$$" ]] && continue # Ignore self
                [[ -n "${seen_pids[$pid]:-}" ]] && continue
                seen_pids["$pid"]=1
                
                cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
                cmdline="${cmdline% }"
                summary+="    -> PID $pid: ${cmdline:-[unknown]}"$'\n'
            fi
        done
        
        if [[ -n "$summary" ]]; then
            log WARN "Processes currently holding the lock:"
            log RAW "${summary%$'\n'}"
        fi
        return 1
    fi

    return 0
}

release_lock() {
    if [[ -n "$LOCK_FD" ]]; then
        exec {LOCK_FD}>&- 2>/dev/null || true
        LOCK_FD=""
    fi
}

check_disk_space() {
    local path="$1"
    local -a lines=()
    local available_mb=0

    mapfile -t lines < <(df -BM --output=avail -- "$path" 2>/dev/null || true)
    if ((${#lines[@]} >= 2)); then
        available_mb="${lines[1]//[^0-9]/}"
    fi
    [[ -n "$available_mb" ]] || available_mb=0

    if ((available_mb < DISK_MIN_FREE_MB)); then
        log ERROR "Low disk space: ${available_mb}MB available at $path (need ${DISK_MIN_FREE_MB}MB)"
        return 1
    fi

    return 0
}

get_available_bytes() {
    local path="$1"
    local -a lines=()
    local available_bytes=0

    mapfile -t lines < <(df -B1 --output=avail -- "$path" 2>/dev/null || true)
    if ((${#lines[@]} >= 2)); then
        available_bytes="${lines[1]//[^0-9]/}"
    fi
    [[ -n "$available_bytes" ]] || available_bytes=0

    printf '%s' "$available_bytes"
}

path_copy_size_bytes() {
    local path="$1"
    local size=0
    local line=""

    if ! path_exists "$path"; then
        printf '0'
        return 0
    fi

    if [[ -d "$path" && ! -L "$path" ]]; then
        line="$(du -sb --apparent-size -- "$path" 2>/dev/null || true)"
        size="${line%%[[:space:]]*}"
    else
        size="$(stat -c '%s' -- "$path" 2>/dev/null || printf '0')"
    fi

    [[ "$size" =~ ^[0-9]+$ ]] || size=0
    printf '%s' "$size"
}

ensure_free_space_for_bytes() {
    local target_path="$1"
    local required_bytes="$2"
    local context="${3:-operation}"
    local available_bytes=0
    local reserve_bytes=$((DISK_COPY_RESERVE_MB * 1024 * 1024))
    local required_mb=0
    local available_mb=0

    (( required_bytes > 0 )) || return 0

    available_bytes="$(get_available_bytes "$target_path")"
    [[ "$available_bytes" =~ ^[0-9]+$ ]] || available_bytes=0

    if (( available_bytes < required_bytes + reserve_bytes )); then
        required_mb=$(( (required_bytes + reserve_bytes + 1048575) / 1048576 ))
        available_mb=$(( (available_bytes + 1048575) / 1048576 ))
        log ERROR "Insufficient free space for ${context}: ${available_mb}MB available, need at least ${required_mb}MB"
        return 1
    fi

    return 0
}

run_logged_command() {
    local -a cmd=( "$@" )
    local rc=0
    local timestamp="" arg=""

    if [[ -z "$LOG_FILE" || ! -w "$LOG_FILE" ]]; then
        # Subshell severs the lock before running unlogged commands
        (
            [[ -n "${LOCK_FD:-}" ]] && exec {LOCK_FD}>&- 2>/dev/null || true
            "${cmd[@]}"
        ) || rc=$?
        return "$rc"
    fi

    printf -v timestamp '%(%H:%M:%S)T' -1
    {
        printf '[%s] [SCRIPT ] BEGIN' "$timestamp"
        for arg in "${cmd[@]}"; do
            printf ' %q' "$arg"
        done
        printf '\n'
    } >> "$LOG_FILE"

    # Subshell severs the lock for BOTH the payload and the asynchronous tee processes
    (
        [[ -n "${LOCK_FD:-}" ]] && exec {LOCK_FD}>&- 2>/dev/null || true
        "${cmd[@]}" > >(tee -a "$LOG_FILE") 2> >(tee -a "$LOG_FILE" >&2)
    ) || rc=$?

    sleep 0.2

    printf -v timestamp '%(%H:%M:%S)T' -1
    printf '[%s] [SCRIPT ] END rc=%d\n' "$timestamp" "$rc" >> "$LOG_FILE"

    return "$rc"
}

auto_prune() {
    if [[ -d "$ACTIVE_LOG_BASE_DIR" ]]; then
        find "$ACTIVE_LOG_BASE_DIR" -type f -name 'dusky_update_*.log' -mtime "+${LOG_RETENTION_DAYS}" -delete \
            2>/dev/null || true
    fi

    if [[ -d "$ACTIVE_BACKUP_BASE_DIR" ]]; then
        find "$ACTIVE_BACKUP_BASE_DIR" -mindepth 1 -maxdepth 1 -type d \
            \( -name 'pre_reset_*' \
            -o -name 'user_mods_*' \
            -o -name 'untracked_collisions_*' \
            -o -name 'needs_merge_*' \
            -o -name 'initial_conflicts_*' \
            -o -name 'repo_history_*' \) \
            -mtime "+${BACKUP_RETENTION_DAYS}" \
            -exec rm -rf {} + 2>/dev/null || true
    fi
}

# ==============================================================================
# GIT HELPERS
# ==============================================================================
detect_git_lock_state() {
    local lock_name="" ref_lock=""

    for lock_name in \
        index.lock \
        config.lock \
        packed-refs.lock \
        shallow.lock \
        HEAD.lock \
        ORIG_HEAD.lock \
        FETCH_HEAD.lock
    do
        if [[ -e "${DOTFILES_GIT_DIR}/${lock_name}" ]]; then
            printf '%s' "$lock_name"
            return 0
        fi
    done

    if [[ -d "${DOTFILES_GIT_DIR}/refs" ]]; then
        while IFS= read -r -d '' ref_lock; do
            printf '%s' "${ref_lock#${DOTFILES_GIT_DIR}/}"
            return 0
        done < <(find "${DOTFILES_GIT_DIR}/refs" -type f -name '*.lock' -print0 2>/dev/null)
    fi

    printf 'none'
}

get_repo_state() {
    local lock_state="" lock_file=""
    local lock_age=0 current_time=0 mtime=0
    local prompt_ans=""
    local can_auto_delete=false
    local lock_real="" fd="" next_lock_state=""
    local lock_open=false

    if [[ -L "$DOTFILES_GIT_DIR" ]]; then
        log ERROR "Git directory must not be a symlink: $DOTFILES_GIT_DIR"
        REPLY="invalid"
        return 0
    fi

    if [[ ! -e "$DOTFILES_GIT_DIR" ]]; then
        REPLY="absent"
        return 0
    fi

    if [[ ! -d "$DOTFILES_GIT_DIR" ]]; then
        log ERROR "Git directory path exists but is not a directory: $DOTFILES_GIT_DIR"
        REPLY="invalid"
        return 0
    fi

    if [[ ! -O "$DOTFILES_GIT_DIR" ]]; then
        log ERROR "Git directory is not owned by the current user: $DOTFILES_GIT_DIR"
        REPLY="invalid"
        return 0
    fi

    if [[ ! -d "$WORK_TREE" || ! -w "$WORK_TREE" ]]; then
        log ERROR "Work tree is not writable: $WORK_TREE"
        REPLY="invalid"
        return 0
    fi

    lock_state="$(detect_git_lock_state)"
    while [[ "$lock_state" != "none" ]]; do
        lock_file="${DOTFILES_GIT_DIR}/${lock_state}"
        can_auto_delete=false
        lock_open=false

        log WARN "Git lock detected: $lock_file"

        current_time="$EPOCHSECONDS"
        mtime="$(stat -c %Y -- "$lock_file" 2>/dev/null || printf '%s' "$current_time")"
        (( lock_age = current_time - mtime )) || lock_age=0

        lock_real="$(readlink -f -- "$lock_file" 2>/dev/null || printf '%s' "$lock_file")"
        for fd in /proc/[0-9]*/fd/*; do
            [[ -e "$fd" ]] || continue
            if [[ "$(readlink -f -- "$fd" 2>/dev/null || true)" == "$lock_real" ]]; then
                lock_open=true
                break
            fi
        done

        if [[ "$lock_open" == true ]]; then
            log ERROR "Lock file is currently open by a live process. Refusing to remove it."
            REPLY="invalid"
            return 0
        fi

        if (( lock_age > 60 )); then
            log INFO "Lock file is ${lock_age}s old and is not open by any live process."
            can_auto_delete=true
        fi

        if [[ "$OPT_DRY_RUN" == true ]]; then
            if [[ "$can_auto_delete" == "true" ]]; then
                log WARN "[DRY-RUN] Stale Git lock detected at $lock_file. Dry-run will not remove it."
            else
                log WARN "[DRY-RUN] Git lock detected at $lock_file. Dry-run will not remove it."
            fi
            REPLY="invalid"
            return 0
        fi

        if [[ -t 0 && "$OPT_FORCE" != true ]]; then
            printf '\n%s[GIT LOCK DETECTED]%s A previous Git operation may have crashed and left a stale lock behind.\n' "$CLR_YLW" "$CLR_RST"
            read -r -t "$PROMPT_TIMEOUT_SHORT" -p "Do you want to clear the lock and continue? [y/N] " prompt_ans || prompt_ans="n"

            if [[ "$prompt_ans" =~ ^[Yy]$ ]]; then
                can_auto_delete=true
            else
                log ERROR "User aborted lock removal."
                REPLY="invalid"
                return 0
            fi
        else
            if [[ "$can_auto_delete" != true ]]; then
                log ERROR "Lock file is too recent (${lock_age}s) to auto-remove in unattended mode."
                log ERROR "Refusing to guess whether another process just exited or is still starting."
                REPLY="invalid"
                return 0
            fi
            log INFO "Auto-removing stale lock file in unattended mode..."
        fi

        rm -f -- "$lock_file" 2>/dev/null || true

        next_lock_state="$(detect_git_lock_state)"
        if [[ "$next_lock_state" == "$lock_state" ]]; then
            log ERROR "Failed to remove lock file: $lock_file"
            log ERROR "Check filesystem permissions or remove it manually."
            REPLY="invalid"
            return 0
        fi

        lock_state="$next_lock_state"
        if [[ "$lock_state" == "none" ]]; then
            log OK "Stale lock successfully cleared."
        fi
    done

    if ! "${GIT_CMD[@]}" rev-parse --git-dir >/dev/null 2>&1; then
        log ERROR "Repository metadata is invalid or corrupted: $DOTFILES_GIT_DIR"
        REPLY="invalid"
        return 0
    fi

    REPLY="valid"
    return 0
}

ensure_repo_defaults() {
    local current_value=""

    current_value="$("${GIT_CMD[@]}" config --get status.showUntrackedFiles 2>/dev/null || true)"
    if [[ "$current_value" != "no" ]]; then
        if [[ "$OPT_DRY_RUN" == true ]]; then
            log INFO "[DRY-RUN] Would set git config: status.showUntrackedFiles=no"
        else
            "${GIT_CMD[@]}" config status.showUntrackedFiles no >/dev/null 2>&1 || true
        fi
    fi
}

detect_git_operation_state() {
    if [[ -d "${DOTFILES_GIT_DIR}/rebase-merge" || -d "${DOTFILES_GIT_DIR}/rebase-apply" ]]; then
        printf 'rebase'
    elif [[ -f "${DOTFILES_GIT_DIR}/MERGE_HEAD" ]]; then
        printf 'merge'
    elif [[ -f "${DOTFILES_GIT_DIR}/CHERRY_PICK_HEAD" ]]; then
        printf 'cherry-pick'
    elif [[ -f "${DOTFILES_GIT_DIR}/REVERT_HEAD" ]]; then
        printf 'revert'
    elif [[ -f "${DOTFILES_GIT_DIR}/BISECT_LOG" ]]; then
        printf 'bisect'
    else
        printf 'none'
    fi
}

normalize_git_state() {
    local op=""
    op="$(detect_git_operation_state)"

    case "$op" in
        none)
            return 0
            ;;
        rebase|merge|cherry-pick|revert)
            log ERROR "Git ${op} is in progress."
            log ERROR "Resolve it manually first to avoid losing conflict-resolution work."
            return 1
            ;;
        bisect)
            log ERROR "Git bisect is in progress. Run 'git --git-dir=\"$DOTFILES_GIT_DIR\" bisect reset' first."
            return 1
            ;;
        *)
            log ERROR "Unknown Git operation state detected: $op"
            return 1
            ;;
    esac
}

canonicalize_git_remote_url() {
    local url="${1-}"

    url="${url%/}"
    url="${url%.git}"

    case "$url" in
        git@github.com:*)
            printf 'github.com/%s' "${url#git@github.com:}"
            ;;
        ssh://git@github.com/*)
            printf 'github.com/%s' "${url#ssh://git@github.com/}"
            ;;
        https://github.com/*)
            printf 'github.com/%s' "${url#https://github.com/}"
            ;;
        http://github.com/*)
            printf 'github.com/%s' "${url#http://github.com/}"
            ;;
        *)
            printf '%s' "$url"
            ;;
    esac
}

get_upstream_fetch_source() {
    local expected_url="" active_remote="" current_url=""

    expected_url="$(canonicalize_git_remote_url "$REPO_URL")"

    for active_remote in origin "$UPSTREAM_REMOTE"; do
        current_url="$("${GIT_CMD[@]}" remote get-url "$active_remote" 2>/dev/null || true)"
        if [[ -n "$current_url" && "$(canonicalize_git_remote_url "$current_url")" == "$expected_url" ]]; then
            REPLY="$active_remote"
            return 0
        fi
    done

    current_url="$("${GIT_CMD[@]}" remote get-url "$UPSTREAM_REMOTE" 2>/dev/null || true)"
    if [[ -n "$current_url" ]]; then
        log WARN "Existing ${UPSTREAM_REMOTE} remote points elsewhere; leaving it unchanged."
    fi

    REPLY="$REPO_URL"
    return 0
}

collect_dir_collision_roots() {
    local root_rel="$1"
    local tracked_exact_name="$2"
    local tracked_desc_name="$3"
    local out_name="$4"

    local -n tracked_exact_ref="$tracked_exact_name"
    local -n tracked_desc_ref="$tracked_desc_name"
    local -n out_ref="$out_name"

    local rel="" abs="" child=""
    local -a stack=()
    local -a children=()
    local last_idx=0

    abs="${WORK_TREE}/${root_rel}"
    [[ -d "$abs" && ! -L "$abs" ]] || return 0

    stack+=("$root_rel")

    while ((${#stack[@]} > 0)); do
        last_idx=$((${#stack[@]} - 1))
        rel="${stack[$last_idx]}"
        unset "stack[$last_idx]"

        abs="${WORK_TREE}/${rel}"
        path_exists "$abs" || continue

        if [[ -L "$abs" || ! -d "$abs" ]]; then
            if [[ -z "${tracked_exact_ref["$rel"]+_}" ]]; then
                out_ref["$rel"]=1
            fi
            continue
        fi

        if [[ -n "${tracked_exact_ref["$rel"]+_}" ]]; then
            out_ref["$rel"]=1
            continue
        fi

        children=()
        while IFS= read -r -d '' child; do
            children+=("$child")
        done < <(find "$abs" -mindepth 1 -maxdepth 1 -printf '%P\0' 2>/dev/null)

        if [[ -n "${tracked_desc_ref["$rel"]+_}" ]]; then
            if ((${#children[@]} == 0)); then
                out_ref["$rel"]=1
            else
                for child in "${children[@]}"; do
                    [[ -n "$child" ]] || continue
                    stack+=("${rel}/${child}")
                done
            fi
        else
            out_ref["$rel"]=1
        fi
    done
}

backup_worktree_collisions_for_ref() {
    local ref="$1"
    local honor_current_tracked="${2:-true}"

    local target_path="" abs="" ancestor="" remaining="" part=""
    local coll_backup_dir="" coll_rel="" coll_src="" coll_dest=""
    local coll_manifest="" info_file=""
    local tracked_path=""
    local required_bytes=0
    local path_bytes=0
    local skip=false
    local -A collision_candidates=()
    local -A collision_roots=()
    local -A mkdir_cache=()
    local -A current_tracked_exact=()
    local -A current_tracked_descendants=()

    if [[ "$honor_current_tracked" == "true" ]]; then
        while IFS= read -r -d '' tracked_path; do
            [[ -n "$tracked_path" ]] || continue
            current_tracked_exact["$tracked_path"]=1

            ancestor="$tracked_path"
            while [[ "$ancestor" == */* ]]; do
                ancestor="${ancestor%/*}"
                current_tracked_descendants["$ancestor"]=1
            done
        done < <("${GIT_CMD[@]}" ls-files -z 2>/dev/null)
    fi

    while IFS= read -r -d '' target_path; do
        [[ -n "$target_path" ]] || continue

        abs="${WORK_TREE}/${target_path}"
        if path_exists "$abs"; then
            if [[ -d "$abs" && ! -L "$abs" ]]; then
                if [[ "$honor_current_tracked" == "true" && -n "${current_tracked_descendants["$target_path"]+_}" ]]; then
                    collect_dir_collision_roots "$target_path" current_tracked_exact current_tracked_descendants collision_candidates
                else
                    collision_candidates["$target_path"]=1
                fi
            elif [[ "$honor_current_tracked" != "true" || -z "${current_tracked_exact["$target_path"]+_}" ]]; then
                collision_candidates["$target_path"]=1
            fi
        fi

        ancestor=""
        remaining="$target_path"
        while [[ "$remaining" == */* ]]; do
            part="${remaining%%/*}"
            if [[ -z "$ancestor" ]]; then
                ancestor="$part"
            else
                ancestor+="/$part"
            fi

            abs="${WORK_TREE}/${ancestor}"
            if path_exists "$abs" && { [[ -L "$abs" ]] || [[ ! -d "$abs" ]]; }; then
                if [[ "$honor_current_tracked" != "true" || -z "${current_tracked_exact["$ancestor"]+_}" ]]; then
                    collision_candidates["$ancestor"]=1
                fi
                break
            fi

            remaining="${remaining#*/}"
        done
    done < <("${GIT_CMD[@]}" ls-tree -r -z --name-only "$ref" 2>/dev/null)

    for coll_rel in "${!collision_candidates[@]}"; do
        skip=false
        ancestor="$coll_rel"

        while [[ "$ancestor" == */* ]]; do
            ancestor="${ancestor%/*}"
            if [[ -n "${collision_candidates["$ancestor"]+_}" ]]; then
                skip=true
                break
            fi
        done

        [[ "$skip" == true ]] && continue
        collision_roots["$coll_rel"]=1
    done

    ((${#collision_roots[@]} > 0)) || return 0

    for coll_rel in "${!collision_roots[@]}"; do
        coll_src="${WORK_TREE}/${coll_rel}"
        path_exists "$coll_src" || continue
        path_bytes="$(path_copy_size_bytes "$coll_src")"
        ((required_bytes += path_bytes))
    done

    check_disk_space "$ACTIVE_BACKUP_BASE_DIR" || return 1
    ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$required_bytes" "collision backup" || return 1

    coll_backup_dir="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "untracked_collisions_${RUN_TIMESTAMP}_XXXXXX")" || {
        log ERROR "Failed to create untracked-collision backup directory"
        return 1
    }
    COLLISION_BACKUP_DIRS+=("$coll_backup_dir")

    coll_manifest="${coll_backup_dir}/MOVED_PATHS.txt"
    info_file="${coll_backup_dir}/INFO.txt"

    : > "$coll_manifest" || {
        log ERROR "Failed to create collision manifest"
        return 1
    }
    chmod 600 -- "$coll_manifest" 2>/dev/null || true

    {
        printf 'Dusky work-tree collision backup\n'
        printf 'Created: %s\n' "$RUN_TIMESTAMP"
        printf 'Reference: %s\n' "$ref"
        printf 'Work tree: %s\n' "$WORK_TREE"
    } > "$info_file" || {
        log ERROR "Failed to create collision info file"
        return 1
    }
    chmod 600 -- "$info_file" 2>/dev/null || true

    log WARN "Found ${#collision_roots[@]} work-tree collision(s). Backing them up..."

    for coll_rel in "${!collision_roots[@]}"; do
        coll_src="${WORK_TREE}/${coll_rel}"
        path_exists "$coll_src" || continue

        coll_dest="${coll_backup_dir}/${coll_rel}"
        ensure_relative_parent_dir "$coll_backup_dir" "$coll_rel" mkdir_cache || {
            log ERROR "Failed to create collision backup directory for: $(quote_for_log "$coll_rel")"
            return 1
        }

        mv -- "$coll_src" "$coll_dest" || {
            log ERROR "Failed to move colliding path: $(quote_for_log "$coll_rel")"
            return 1
        }

        COLLISION_MOVED_PATHS["$coll_rel"]=1

        printf '%q\n' "$coll_rel" >> "$coll_manifest" || {
            log ERROR "Failed to record colliding path: $(quote_for_log "$coll_rel")"
            return 1
        }

        log RAW "  → Backed up collision: $(quote_for_log "$coll_rel")"
    done

    log OK "Collisions backed up to: $coll_backup_dir"
    return 0
}

fetch_with_retry() {
    local source="${1:?missing fetch source}"
    local attempt=1
    local wait_time=$FETCH_INITIAL_BACKOFF
    local rc=0

    while (( attempt <= FETCH_MAX_ATTEMPTS )); do
        if timeout "${FETCH_TIMEOUT}s" \
            "${GIT_CMD[@]}" fetch --no-write-fetch-head "$source" \
            "+refs/heads/${BRANCH}:${UPSTREAM_TRACKING_REF}" \
            >> "$LOG_FILE" 2>&1; then
            return 0
        fi

        rc=$?
        if (( attempt < FETCH_MAX_ATTEMPTS )); then
            if (( rc == 124 )); then
                log WARN "Fetch attempt $attempt/$FETCH_MAX_ATTEMPTS timed out. Retrying in ${wait_time}s..."
            else
                log WARN "Fetch attempt $attempt/$FETCH_MAX_ATTEMPTS failed. Retrying in ${wait_time}s..."
            fi
            sleep "$wait_time"
            (( wait_time *= 2 ))
        fi
        (( attempt++ ))
    done

    if (( rc == 124 )); then
        log ERROR "Fetch failed after $FETCH_MAX_ATTEMPTS attempts due to repeated timeouts"
    else
        log ERROR "Fetch failed after $FETCH_MAX_ATTEMPTS attempts"
    fi
    return 1
}

clone_with_retry() {
    local -i attempt=1
    local -i wait_time=$FETCH_INITIAL_BACKOFF
    local -i rc=0

    while (( attempt <= FETCH_MAX_ATTEMPTS )); do
        if timeout "${CLONE_TIMEOUT}s" \
            "$GIT_BIN" clone --bare --branch "$BRANCH" "$REPO_URL" "$DOTFILES_GIT_DIR" \
            >> "$LOG_FILE" 2>&1; then
            return 0
        fi

        rc=$?
        rm -rf -- "$DOTFILES_GIT_DIR" 2>/dev/null || true

        if (( attempt < FETCH_MAX_ATTEMPTS )); then
            if (( rc == 124 )); then
                log WARN "Clone attempt $attempt/$FETCH_MAX_ATTEMPTS timed out. Retrying in ${wait_time}s..."
            else
                log WARN "Clone attempt $attempt/$FETCH_MAX_ATTEMPTS failed. Retrying in ${wait_time}s..."
            fi
            sleep "$wait_time"
            (( wait_time *= 2 ))
        fi

        (( attempt++ ))
    done

    if (( rc == 124 )); then
        log ERROR "Clone failed after $FETCH_MAX_ATTEMPTS attempts due to repeated timeouts"
    else
        log ERROR "Clone failed after $FETCH_MAX_ATTEMPTS attempts"
    fi
    return 1
}

show_update_preview() {
    local local_head="$1"
    local remote_head="$2"
    local base_commit="${3:-}"
    local diff_base="" commit_count="?"
    local -a changed_files=()

    diff_base="$local_head"
    [[ -n "$base_commit" ]] && diff_base="$base_commit"

    commit_count="$("${GIT_CMD[@]}" rev-list --count "${local_head}..${remote_head}" 2>/dev/null || printf '?')"
    mapfile -d '' -t changed_files < <("${GIT_CMD[@]}" diff -z --name-only "${diff_base}..${remote_head}" 2>/dev/null || true)

    printf '\n'
    log INFO "Upstream changes:"
    printf '    Commits behind:  %s\n' "$commit_count"
    printf '    Files changed:   %d\n' "${#changed_files[@]}"

    if [[ "$commit_count" != "?" ]] && ((commit_count > 0)); then
        printf '\n    Recent commits:\n'
        "${GIT_CMD[@]}" log --oneline --no-decorate -10 "${local_head}..${remote_head}" 2>/dev/null | \
            while IFS= read -r line; do
                printf '      %s\n' "$line"
            done || true
        if ((commit_count > 10)); then
            printf '      ... and %d more\n' "$((commit_count - 10))"
        fi
    fi
    printf '\n'
}

git_head_path_meta() {
    local path="$1"
    local record="" meta="" mode="" type="" oid=""

    if IFS= read -r -d '' record < <("${GIT_CMD[@]}" ls-tree -z HEAD -- "$path" 2>/dev/null); then
        meta="${record%%$'\t'*}"
        read -r mode type oid <<< "$meta"
        printf '%s\t%s' "$mode" "$oid"
    else
        printf ''
    fi
}

handle_unrelated_upstream_history() {
    local remote_ref="${1:?missing remote ref}"
    local sync_choice="1"

    log WARN "Local repository does not share history with ${remote_ref}."

    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "[DRY-RUN] Would back up the current tracked tree and Git history, then reset to ${remote_ref}"
        return 0
    fi

    if [[ -t 0 ]]; then
        printf '\n%s[UNRELATED HISTORY]%s The existing bare repo at %s is not based on Dusky upstream.\n' \
            "$CLR_YLW" "$CLR_RST" "$DOTFILES_GIT_DIR"
        printf '  1) Abort (keep current state) [DEFAULT]\n'
        printf '  %s2) Replace local repo contents with upstream [RECOMMENDED]%s\n' \
            "$CLR_GRN" "$CLR_RST"
        printf '     Current tracked files and Git history will be backed up before reset.\n\n'

        read -r -t "$PROMPT_TIMEOUT_LONG" -p "Choice [1-2] (default: 1): " sync_choice 2>/dev/null || sync_choice="1"
    elif [[ "$OPT_ALLOW_DIVERGED_RESET" == true ]]; then
        sync_choice="2"
    else
        log ERROR "Non-interactive mode and unrelated history. Aborting to prevent data loss (use --allow-diverged-reset to override)."
        return "$SYNC_RC_RECOVERABLE"
    fi

    sync_choice="${sync_choice:-1}"

    case "$sync_choice" in
        1)
            log INFO "Aborted by user."
            return "$SYNC_RC_RECOVERABLE"
            ;;
        2)
            backup_git_history || return "$SYNC_RC_UNSAFE"
            backup_worktree_collisions_for_ref "$remote_ref" true || return "$SYNC_RC_UNSAFE"
            backup_full_tracked_tree || return "$SYNC_RC_UNSAFE"

            log INFO "Resetting to ${remote_ref}..."
            if "${GIT_CMD[@]}" reset --hard "$remote_ref" >> "$LOG_FILE" 2>&1; then
                log OK "Reset complete."
                log WARN "Previous tracked files were preserved in a full backup and were not auto-restored because the histories are unrelated."
                log INFO "Review the preserved backup at: $FULL_TRACKED_BACKUP_DIR"
            else
                log ERROR "Reset failed."
                return "$SYNC_RC_UNSAFE"
            fi
            ;;
        *)
            log INFO "Invalid choice. Aborting."
            return "$SYNC_RC_RECOVERABLE"
            ;;
    esac

    return 0
}

# ==============================================================================
# CHANGE MANIFEST / BACKUP / RESTORE
# ==============================================================================
capture_tracked_changes_manifest() {
    local -a raw_records=()
    local meta="" path="" oldmode="" newmode="" oldoid="" newoid="" status=""
    local -i i=0
    local -i count=0
    local -i parsed_count=0

    CHANGE_PATHS=()
    CHANGE_STATUS=()
    CHANGE_OLD_MODE=()
    CHANGE_OLD_OID=()
    CHANGE_BACKUP_HAS_FILE=()

    "${GIT_CMD[@]}" update-index -q --refresh >/dev/null 2>&1 || true
    mapfile -d '' -t raw_records < <("${GIT_CMD[@]}" diff-index --raw --no-renames -z HEAD -- 2>/dev/null || true)

    count="${#raw_records[@]}"

    while (( i < count )); do
        meta="${raw_records[i]}"
        path="${raw_records[i+1]:-}"
        (( i += 2 ))

        [[ -n "$meta" ]] || continue

        read -r oldmode newmode oldoid newoid status <<< "${meta#:}"
        status="${status%%[0-9]*}"

        [[ -n "$path" ]] || continue

        CHANGE_PATHS+=("$path")
        CHANGE_STATUS["$path"]="$status"
        CHANGE_OLD_MODE["$path"]="$oldmode"
        CHANGE_OLD_OID["$path"]="$oldoid"
        CHANGE_BACKUP_HAS_FILE["$path"]=0

        (( parsed_count++ ))
    done

    if (( count > 1 && parsed_count == 0 )); then
        log ERROR "Git reported tracked changes, but the engine failed to parse them."
        log ERROR "Raw Git output length: $count items."
        log ERROR "FATAL: Aborting to prevent accidental data wipe during reset."
        return 1
    fi

    return 0
}

ensure_relative_parent_dir() {
    local root="$1"
    local rel="$2"
    local -n cache_ref="$3"
    local parent_rel="."
    local parent_abs="$root"

    if [[ "$rel" == */* ]]; then
        parent_rel="${rel%/*}"
        parent_abs="${root}/${parent_rel}"
    fi

    if [[ -n "${cache_ref["$parent_abs"]:-}" ]]; then
        return 0
    fi

    mkdir -p -- "$parent_abs" || return 1
    cache_ref["$parent_abs"]=1
    return 0
}

backup_user_modifications() {
    local backup_dir="" manifest_file="" path="" status="" src="" dest="" qpath=""
    local copied_count=0
    local required_bytes=0
    local path_bytes=0
    local -A mkdir_cache=()

    if [[ -n "$USER_MODS_BACKUP_DIR" && -d "$USER_MODS_BACKUP_DIR" ]]; then
        return 0
    fi
    ((${#CHANGE_PATHS[@]} > 0)) || return 0

    for path in "${CHANGE_PATHS[@]}"; do
        status="${CHANGE_STATUS["$path"]:-?}"
        src="${WORK_TREE}/${path}"

        if [[ "$status" == "D" ]] || ! path_exists "$src"; then
            continue
        fi

        path_bytes="$(path_copy_size_bytes "$src")"
        ((required_bytes += path_bytes))
    done

    check_disk_space "$ACTIVE_BACKUP_BASE_DIR" || return 1
    ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$required_bytes" "modified-files backup" || return 1

    backup_dir="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "user_mods_${RUN_TIMESTAMP}_XXXXXX")" || {
        log ERROR "Failed to create modified-files backup directory"
        return 1
    }
    manifest_file="${backup_dir}/MANIFEST.txt"
    : > "$manifest_file" || {
        log ERROR "Failed to create backup manifest"
        return 1
    }
    chmod 600 -- "$manifest_file" 2>/dev/null || true

    USER_MODS_BACKUP_DIR="$backup_dir"

    for path in "${CHANGE_PATHS[@]}"; do
        status="${CHANGE_STATUS["$path"]:-?}"
        src="${WORK_TREE}/${path}"
        printf -v qpath '%q' "$path"

        if [[ "$status" == "D" || ! -e "$src" && ! -L "$src" ]]; then
            CHANGE_BACKUP_HAS_FILE["$path"]=0
            printf 'status=%s old_oid=%s has_copy=0 path=%s\n' \
                "$status" "${CHANGE_OLD_OID["$path"]:-}" "$qpath" >> "$manifest_file"
            continue
        fi

        dest="${backup_dir}/${path}"
        ensure_relative_parent_dir "$backup_dir" "$path" mkdir_cache || {
            log ERROR "Failed to create backup parent directory for $(quote_for_log "$path")"
            return 1
        }

        cp -a --reflink=auto -- "$src" "$dest" || {
            log ERROR "Failed to back up modified file: $(quote_for_log "$path")"
            return 1
        }

        CHANGE_BACKUP_HAS_FILE["$path"]=1
        printf 'status=%s old_oid=%s has_copy=1 path=%s\n' \
            "$status" "${CHANGE_OLD_OID["$path"]:-}" "$qpath" >> "$manifest_file"
        ((copied_count++)) || true
    done

    log OK "Backed up ${#CHANGE_PATHS[@]} tracked change(s) to: $backup_dir"
    if ((copied_count == 0)); then
        log INFO "Tracked changes were deletion-only; backup manifest preserved deletion intent"
    fi

    return 0
}

backup_full_tracked_tree() {
    local backup_dir="" info_file="" path="" src="" dest=""
    local copied_count=0
    local required_bytes=0
    local path_bytes=0
    local -A mkdir_cache=()
    local -a tracked_paths=()

    if [[ -n "$FULL_TRACKED_BACKUP_DIR" && -d "$FULL_TRACKED_BACKUP_DIR" ]]; then
        return 0
    fi

    mapfile -d '' -t tracked_paths < <("${GIT_CMD[@]}" ls-files -z 2>/dev/null)

    for path in "${tracked_paths[@]}"; do
        src="${WORK_TREE}/${path}"
        path_exists "$src" || continue
        path_bytes="$(path_copy_size_bytes "$src")"
        ((required_bytes += path_bytes))
    done

    check_disk_space "$ACTIVE_BACKUP_BASE_DIR" || return 1
    ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$required_bytes" "full tracked-tree backup" || return 1

    backup_dir="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "pre_reset_${RUN_TIMESTAMP}_XXXXXX")" || {
        log ERROR "Failed to create full tracked backup directory"
        return 1
    }
    info_file="${backup_dir}/INFO.txt"
    : > "$info_file" || true
    chmod 600 -- "$info_file" 2>/dev/null || true

    printf 'Dusky full tracked-tree backup\n' >> "$info_file"
    printf 'Created: %s\n' "$RUN_TIMESTAMP" >> "$info_file"
    printf 'Repository HEAD before destructive action: %s\n' "$("${GIT_CMD[@]}" rev-parse HEAD 2>/dev/null || printf 'unknown')" >> "$info_file"

    for path in "${tracked_paths[@]}"; do
        src="${WORK_TREE}/${path}"
        if ! path_exists "$src"; then
            continue
        fi

        dest="${backup_dir}/${path}"
        ensure_relative_parent_dir "$backup_dir" "$path" mkdir_cache || {
            log ERROR "Failed to create tracked-backup directory for $(quote_for_log "$path")"
            return 1
        }

        cp -a --reflink=auto -- "$src" "$dest" || {
            log ERROR "Failed to back up tracked file: $(quote_for_log "$path")"
            return 1
        }
        ((copied_count++)) || true
    done

    FULL_TRACKED_BACKUP_DIR="$backup_dir"
    log OK "Full tracked-tree backup preserved at: $backup_dir ($copied_count file(s))"
    return 0
}

backup_git_history() {
    local backup_root="" backup_repo="" info_file=""
    local required_bytes=0

    if [[ -n "$GIT_HISTORY_BACKUP_DIR" && -d "$GIT_HISTORY_BACKUP_DIR" ]]; then
        return 0
    fi

    required_bytes="$(path_copy_size_bytes "$DOTFILES_GIT_DIR")"
    check_disk_space "$ACTIVE_BACKUP_BASE_DIR" || return 1
    ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$required_bytes" "Git history backup" || return 1

    backup_root="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "repo_history_${RUN_TIMESTAMP}_XXXXXX")" || {
        log ERROR "Failed to create Git history backup directory"
        return 1
    }

    backup_repo="${backup_root}/repo.git"
    cp -a --reflink=auto -- "$DOTFILES_GIT_DIR" "$backup_repo" || {
        log ERROR "Failed to preserve Git history backup"
        return 1
    }

    info_file="${backup_root}/INFO.txt"
    {
        printf 'Dusky Git history backup\n'
        printf 'Created: %s\n' "$RUN_TIMESTAMP"
        printf 'Source: %s\n' "$DOTFILES_GIT_DIR"
    } > "$info_file" || true
    chmod 600 -- "$info_file" 2>/dev/null || true

    GIT_HISTORY_BACKUP_DIR="$backup_root"
    log OK "Git history backup preserved at: $backup_root"
    return 0
}

ensure_merge_dir() {
    if [[ -n "$MERGE_DIR" && -d "$MERGE_DIR" ]]; then
        return 0
    fi

    MERGE_DIR="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "needs_merge_${RUN_TIMESTAMP}_XXXXXX")" || {
        log ERROR "Failed to create merge directory"
        return 1
    }
    return 0
}

path_has_collision_backup() {
    local path="$1"
    local moved_path=""

    if [[ -n "${COLLISION_MOVED_PATHS["$path"]+_}" ]]; then
        return 0
    fi

    for moved_path in "${!COLLISION_MOVED_PATHS[@]}"; do
        [[ "$moved_path" == "$path/"* ]] && return 0
    done

    return 1
}

classify_restore_action() {
    local path="$1"
    local status="$2"
    local old_mode="$3"
    local old_oid="$4"

    local head_meta="" new_mode="" new_oid="" action=""
    local old_oid_valid=false
    local safe_restore=false

    head_meta="$(git_head_path_meta "$path")"
    if [[ -n "$head_meta" ]]; then
        IFS=$'\t' read -r new_mode new_oid <<< "$head_meta"
    fi

    if [[ -n "$old_oid" && "$old_oid" != "0000000000000000000000000000000000000000" ]]; then
        old_oid_valid=true
    fi

    if [[ "$status" == "D" ]]; then
        if path_has_collision_backup "$path"; then
            action="delete-merge"
        elif [[ -z "$new_oid" ]]; then
            action="delete-preserved"
        elif [[ "$old_oid_valid" == true && "$new_oid" == "$old_oid" && "$new_mode" == "$old_mode" ]]; then
            action="delete-safe"
        else
            action="delete-merge"
        fi

        printf '%s\t%s\t%s' "$action" "$new_mode" "$new_oid"
        return 0
    fi

    if [[ "$old_oid_valid" == true ]]; then
        if [[ -n "$new_oid" && "$new_oid" == "$old_oid" && "$new_mode" == "$old_mode" ]]; then
            safe_restore=true
        fi
    else
        if [[ -z "$new_oid" ]]; then
            safe_restore=true
        fi
    fi

    if [[ "$safe_restore" == true ]]; then
        action="restore"
    else
        action="merge"
    fi

    printf '%s\t%s\t%s' "$action" "$new_mode" "$new_oid"
}

atomic_restore_path() {
    local src="$1"
    local target="$2"
    local parent="" base="" probe_path="" tmpdir="" tmp="" displaced=""
    local -i copy_bytes=0
    local mv_rc=0

    parent="$(path_parent "$target")"
    base="$(path_base "$target")"

    mkdir -p -- "$parent" || return 1

    copy_bytes="$(path_copy_size_bytes "$src")"
    if (( copy_bytes > 0 )); then
        probe_path="$(nearest_existing_ancestor "$parent")"
        ensure_free_space_for_bytes "$probe_path" "$copy_bytes" "restoring $(quote_for_log "$target")" || return 1
    fi

    tmpdir="$(mktemp -d -p "$parent" ".${base}.dusky_tmp.XXXXXX")" || return 1
    CREATED_TEMP_DIRS+=("$tmpdir")

    tmp="${tmpdir}/${base}"
    displaced="${tmpdir}/.old_${base}"

    cp -a --reflink=auto -- "$src" "$tmp" || return 1

    if path_exists "$target"; then
        mv -fT -- "$target" "$displaced" || return 1
    fi

    if mv -fT -- "$tmp" "$target"; then
        rm -rf -- "$tmpdir" 2>/dev/null || true
        return 0
    fi

    mv_rc=$?
    if path_exists "$displaced" && ! path_exists "$target"; then
        mv -fT -- "$displaced" "$target" 2>/dev/null || true
    fi

    return "$mv_rc"
}

restore_user_modifications() {
    local path="" status="" old_mode="" old_oid="" backup_src="" target="" merge_dest="" marker=""
    local plan="" action="" new_mode="" new_oid=""
    local probe_path="" device_id=""
    local all_ok=true
    local -i restored_count=0
    local -i merge_count=0
    local -i deletion_count=0
    local -i merge_required_bytes=0
    local -i backup_bytes=0
    local -i target_bytes=0
    local -i cumulative_delta=0
    local -i current_required=0
    local -i peak_required=0
    local -A mkdir_cache=()
    local -A restore_device_probe=()
    local -A restore_device_peak=()
    local -A restore_device_delta=()
    local -A planned_action=()
    local -A planned_new_mode=()
    local -A planned_new_oid=()

    if [[ -z "$USER_MODS_BACKUP_DIR" || ! -d "$USER_MODS_BACKUP_DIR" ]]; then
        return 0
    fi

    (( ${#CHANGE_PATHS[@]} > 0 )) || return 0

    for path in "${CHANGE_PATHS[@]}"; do
        status="${CHANGE_STATUS["$path"]:-?}"
        old_mode="${CHANGE_OLD_MODE["$path"]:-}"
        old_oid="${CHANGE_OLD_OID["$path"]:-}"
        backup_src="${USER_MODS_BACKUP_DIR}/${path}"
        target="${WORK_TREE}/${path}"

        if [[ "$status" != "D" ]]; then
            if [[ "${CHANGE_BACKUP_HAS_FILE["$path"]:-0}" != "1" ]]; then
                continue
            fi
            if [[ ! -e "$backup_src" && ! -L "$backup_src" ]]; then
                continue
            fi
        fi

        plan="$(classify_restore_action "$path" "$status" "$old_mode" "$old_oid")"
        IFS=$'\t' read -r action new_mode new_oid <<< "$plan"

        planned_action["$path"]="$action"
        planned_new_mode["$path"]="$new_mode"
        planned_new_oid["$path"]="$new_oid"

        case "$action" in
            restore)
                backup_bytes="$(path_copy_size_bytes "$backup_src")"
                target_bytes=0
                if path_exists "$target"; then
                    target_bytes="$(path_copy_size_bytes "$target")"
                fi

                probe_path="$(nearest_existing_ancestor "$(path_parent "$target")")"
                device_id="$(path_device_id "$probe_path")" || {
                    log ERROR "Failed to determine filesystem for restore target: $(quote_for_log "$path")"
                    return 1
                }

                cumulative_delta="${restore_device_delta["$device_id"]:-0}"
                peak_required="${restore_device_peak["$device_id"]:-0}"
                current_required=$(( cumulative_delta + backup_bytes ))

                if (( current_required > peak_required )); then
                    restore_device_peak["$device_id"]=$current_required
                fi

                restore_device_delta["$device_id"]=$(( cumulative_delta + backup_bytes - target_bytes ))

                if [[ -z "${restore_device_probe["$device_id"]:-}" ]]; then
                    restore_device_probe["$device_id"]="$probe_path"
                fi
                ;;
            merge)
                backup_bytes="$(path_copy_size_bytes "$backup_src")"
                (( merge_required_bytes += backup_bytes ))
                ;;
        esac
    done

    for device_id in "${!restore_device_peak[@]}"; do
        peak_required="${restore_device_peak["$device_id"]:-0}"
        (( peak_required > 0 )) || continue
        probe_path="${restore_device_probe["$device_id"]}"
        ensure_free_space_for_bytes "$probe_path" "$peak_required" "tracked-change restoration" || return 1
    done

    if (( merge_required_bytes > 0 )); then
        ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$merge_required_bytes" "manual-merge copies" || return 1
    fi

    log INFO "Restoring your tracked changes..."

    for path in "${CHANGE_PATHS[@]}"; do
        status="${CHANGE_STATUS["$path"]:-?}"
        old_mode="${CHANGE_OLD_MODE["$path"]:-}"
        old_oid="${CHANGE_OLD_OID["$path"]:-}"
        backup_src="${USER_MODS_BACKUP_DIR}/${path}"
        target="${WORK_TREE}/${path}"

        action="${planned_action["$path"]:-}"
        new_mode="${planned_new_mode["$path"]:-}"
        new_oid="${planned_new_oid["$path"]:-}"

        [[ -n "$action" ]] || continue

        case "$action" in
            delete-preserved)
                (( deletion_count++ )) || true
                ;;
            delete-safe)
                rm -rf -- "$target" || {
                    log ERROR "Failed to re-apply tracked deletion for: $(quote_for_log "$path")"
                    all_ok=false
                    continue
                }
                (( deletion_count++ )) || true
                ;;
            delete-merge)
                ensure_merge_dir || {
                    all_ok=false
                    continue
                }

                marker="${MERGE_DIR}/${path}.dusky_deleted"
                ensure_relative_parent_dir "$MERGE_DIR" "${path}.dusky_deleted" mkdir_cache || {
                    log ERROR "Failed to create deletion-marker directory for: $(quote_for_log "$path")"
                    all_ok=false
                    continue
                }

                {
                    printf 'Tracked deletion requires manual review.\n'
                    printf 'Path: %q\n' "$path"
                    printf 'Old HEAD mode: %s\n' "$old_mode"
                    printf 'Old HEAD object: %s\n' "$old_oid"
                    printf 'Current HEAD mode: %s\n' "${new_mode:-<absent>}"
                    printf 'Current HEAD object: %s\n' "${new_oid:-<absent>}"
                } > "$marker" || {
                    log ERROR "Failed to write deletion marker for: $(quote_for_log "$path")"
                    all_ok=false
                    continue
                }

                chmod 600 -- "$marker" 2>/dev/null || true
                (( merge_count++ )) || true
                log RAW "  → Manual review needed for tracked deletion: $(quote_for_log "$path")"
                ;;
            restore)
                if atomic_restore_path "$backup_src" "$target"; then
                    (( restored_count++ )) || true
                    log RAW "  → Restored: $(quote_for_log "$path")"
                else
                    log ERROR "Failed to restore: $(quote_for_log "$path")"
                    all_ok=false
                fi
                ;;
            merge)
                ensure_merge_dir || {
                    all_ok=false
                    continue
                }

                merge_dest="${MERGE_DIR}/${path}"
                ensure_relative_parent_dir "$MERGE_DIR" "$path" mkdir_cache || {
                    log ERROR "Failed to create merge directory for: $(quote_for_log "$path")"
                    all_ok=false
                    continue
                }

                cp -a --reflink=auto -- "$backup_src" "$merge_dest" || {
                    log ERROR "Failed to save merge copy for: $(quote_for_log "$path")"
                    all_ok=false
                    continue
                }

                (( merge_count++ )) || true
                log RAW "  → Upstream changed: $(quote_for_log "$path") (your version saved for merge)"
                ;;
            *)
                log ERROR "Unknown restore action for: $(quote_for_log "$path")"
                all_ok=false
                ;;
        esac
    done

    if (( restored_count > 0 )); then
        log OK "Auto-restored $restored_count file(s) (upstream had not changed them)"
    fi
    if (( merge_count > 0 )); then
        log WARN "$merge_count file(s) need manual merge — upstream changed them too or a path conflict was preserved"
        log INFO "Review saved files and markers in: $MERGE_DIR"
    fi
    if (( deletion_count > 0 )); then
        log WARN "$deletion_count tracked deletion(s) preserved or queued for manual merge"
    fi
    if (( restored_count == 0 && merge_count == 0 && deletion_count == 0 )); then
        log INFO "No modifications needed restoring."
    fi

    if [[ "$all_ok" == true ]]; then
        rm -rf -- "$USER_MODS_BACKUP_DIR" 2>/dev/null || true
        USER_MODS_BACKUP_DIR=""
        return 0
    fi

    log ERROR "Some files could not be correctly processed. Backup preserved at: $USER_MODS_BACKUP_DIR"
    return 1
}

# ==============================================================================
# INITIAL CLONE
# ==============================================================================
initial_clone() {
    log SECTION "First-Time Setup"
    log INFO "Bare repository not found at: $DOTFILES_GIT_DIR"

    local do_clone="y"

    [[ -d "$WORK_TREE" && -w "$WORK_TREE" ]] || {
        log ERROR "Work tree is not writable: $WORK_TREE"
        return "$SYNC_RC_UNSAFE"
    }

    if [[ -t 0 && "$OPT_FORCE" != true ]]; then
        printf '\n'
        read -r -t "$PROMPT_TIMEOUT_LONG" -p "Clone from ${REPO_URL}? [y/N] " do_clone || do_clone="n"
        do_clone="${do_clone:-n}"
    fi

    if [[ ! "$do_clone" =~ ^[Yy]$ ]]; then
        log INFO "Clone cancelled."
        return "$SYNC_RC_RECOVERABLE"
    fi

    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "[DRY-RUN] Would clone branch ${BRANCH}: $REPO_URL → $DOTFILES_GIT_DIR"
        return 0
    fi

    log INFO "Cloning bare repository..."
    clone_with_retry || return "$SYNC_RC_UNSAFE"

    ensure_repo_defaults

    log INFO "Checking out files..."
    backup_worktree_collisions_for_ref "HEAD" false || return "$SYNC_RC_UNSAFE"

    if ! "${GIT_CMD[@]}" checkout >> "$LOG_FILE" 2>&1; then
        log ERROR "Checkout failed. Repository may be in an inconsistent state."
        return "$SYNC_RC_UNSAFE"
    fi

    log OK "Repository cloned and checked out successfully."
    return 0
}

initialize_unborn_repo_from_ref() {
    local remote_ref="${1:?missing remote ref}"

    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "[DRY-RUN] Would initialize unborn repository from ${remote_ref}"
        return 0
    fi

    "${GIT_CMD[@]}" symbolic-ref HEAD "refs/heads/${BRANCH}" >> "$LOG_FILE" 2>&1 || {
        log ERROR "Failed to point HEAD at refs/heads/${BRANCH}"
        return 1
    }

    backup_worktree_collisions_for_ref "$remote_ref" false || return 1

    if "${GIT_CMD[@]}" reset --hard "$remote_ref" >> "$LOG_FILE" 2>&1; then
        log OK "Initialized existing empty repository from upstream."
        return 0
    fi

    log ERROR "Failed to initialize unborn repository from upstream."
    return 1
}

# ==============================================================================
# PULL UPDATES
# ==============================================================================
pull_updates() {
    log SECTION "Synchronizing Dotfiles Repository"

    local repo_state=""
    local fetch_source="" remote_ref="$UPSTREAM_TRACKING_REF"
    local local_head="" remote_head="" base_commit=""
    local sync_choice="1"
    local rebase_output="" rebase_rc=0
    local clone_rc=0
    local mb_rc=0

    get_repo_state
    repo_state="$REPLY"

    case "$repo_state" in
        absent)
            if initial_clone; then
                log OK "Repository synchronized (initial clone)."
                return 0
            fi
            clone_rc=$?
            return "$clone_rc"
            ;;
        invalid)
            return "$SYNC_RC_UNSAFE"
            ;;
        valid)
            ;;
        *)
            log ERROR "Unknown repository state: $repo_state"
            return "$SYNC_RC_UNSAFE"
            ;;
    esac

    normalize_git_state || return "$SYNC_RC_UNSAFE"
    get_upstream_fetch_source || return "$SYNC_RC_UNSAFE"
    fetch_source="$REPLY"

    log INFO "Fetching from upstream..."
    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "[DRY-RUN] Would fetch branch ${BRANCH} from ${fetch_source}"
    else
        fetch_with_retry "$fetch_source" || return "$SYNC_RC_RECOVERABLE"
        log OK "Fetch complete."
    fi

    log INFO "Checking sync status..."
    local_head="$("${GIT_CMD[@]}" rev-parse --verify -q HEAD 2>/dev/null || true)"
    remote_head="$("${GIT_CMD[@]}" rev-parse --verify -q "$remote_ref" 2>/dev/null || true)"

    if [[ -z "$remote_head" ]]; then
        if [[ "$OPT_DRY_RUN" == true ]]; then
            log WARN "[DRY-RUN] No cached upstream ref found. Cannot preview sync status."
            return 0
        fi
        log ERROR "Cannot determine upstream HEAD for ${BRANCH}"
        return "$SYNC_RC_UNSAFE"
    fi

    if [[ -z "$local_head" ]]; then
        if [[ "$OPT_DRY_RUN" == true ]]; then
            log INFO "[DRY-RUN] Existing repository has an unborn HEAD. Would initialize it from ${remote_ref}."
            return 0
        fi

        log WARN "Local repository has no commits yet. Initializing it from upstream..."
        initialize_unborn_repo_from_ref "$remote_ref" || return "$SYNC_RC_UNSAFE"
        ensure_repo_defaults
        log OK "Repository synchronized."
        return 0
    fi

    if [[ "$local_head" == "$remote_head" ]]; then
        local unhealthy_tracked=0
        local changed_path="" changed_status=""

        capture_tracked_changes_manifest || return "$SYNC_RC_UNSAFE"

        for changed_path in "${CHANGE_PATHS[@]}"; do
            changed_status="${CHANGE_STATUS["$changed_path"]:-}"
            case "$changed_status" in
                D|T)
                    (( unhealthy_tracked++ )) || true
                    ;;
            esac
        done

        if (( unhealthy_tracked > 0 )); then
            log WARN "HEAD matches ${remote_ref}, but ${unhealthy_tracked} tracked path(s) are missing or type-mismatched in the work tree."
            log INFO "Leaving local files untouched because those paths may be intentional user changes."
        else
            log OK "Already up to date."
        fi

        [[ "$OPT_DRY_RUN" == true ]] || ensure_repo_defaults
        return 0
    fi

    base_commit="$("${GIT_CMD[@]}" merge-base "$local_head" "$remote_head" 2>/dev/null)" || mb_rc=$?
    if (( mb_rc == 1 )) || [[ "$mb_rc" -eq 0 && -z "$base_commit" ]]; then
        if handle_unrelated_upstream_history "$remote_ref"; then
            [[ "$OPT_DRY_RUN" == true ]] || ensure_repo_defaults
            [[ "$OPT_DRY_RUN" == true ]] || log OK "Repository synchronized."
            return 0
        else
            return $?
        fi
    elif (( mb_rc != 0 )); then
        log ERROR "Cannot determine merge-base with upstream (git exit code $mb_rc). Repository may be corrupted."
        return "$SYNC_RC_UNSAFE"
    fi

    show_update_preview "$local_head" "$remote_head" "$base_commit"

    if [[ "$base_commit" == "$local_head" ]]; then
        log INFO "Fast-forwarding to upstream..."

        if [[ "$OPT_DRY_RUN" == true ]]; then
            log INFO "[DRY-RUN] Would reset --hard to ${remote_ref}"
            return 0
        fi

        backup_worktree_collisions_for_ref "$remote_ref" true || return "$SYNC_RC_UNSAFE"
        capture_tracked_changes_manifest || return "$SYNC_RC_UNSAFE"
        backup_user_modifications || {
            log ERROR "Backup failed. Aborting update to protect your files."
            return "$SYNC_RC_UNSAFE"
        }

        if "${GIT_CMD[@]}" reset --hard "$remote_ref" >> "$LOG_FILE" 2>&1; then
            log OK "Updated to latest."
            restore_user_modifications || return "$SYNC_RC_UNSAFE"
            ensure_repo_defaults
        else
            log ERROR "Reset failed."
            return "$SYNC_RC_UNSAFE"
        fi
    else
        log WARN "Local history diverged from upstream."

        if [[ "$OPT_DRY_RUN" == true ]]; then
            log INFO "[DRY-RUN] History diverged. Would require reset or rebase to ${remote_ref}."
            return 0
        fi

        if [[ -t 0 ]]; then
            printf '\n%s[DIVERGED HISTORY]%s Choose sync method:\n' "$CLR_YLW" "$CLR_RST"
            printf '  1) Abort (keep current state) [DEFAULT]\n'
            printf '  %s2) Reset to upstream [RECOMMENDED]%s\n' "$CLR_GRN" "$CLR_RST"
            printf '     Your uncommitted tweaks will be backed up and auto-restored where safe.\n'
            printf '  3) Attempt rebase (may fail)\n\n'
            read -r -t "$PROMPT_TIMEOUT_LONG" -p "Choice [1-3] (default: 1): " sync_choice 2>/dev/null || sync_choice="1"
        elif [[ "$OPT_ALLOW_DIVERGED_RESET" == true ]]; then
            sync_choice="2"
        else
            log ERROR "Non-interactive mode and diverged history. Aborting to prevent data loss (use --allow-diverged-reset to override)."
            return "$SYNC_RC_RECOVERABLE"
        fi

        sync_choice="${sync_choice:-1}"

        case "$sync_choice" in
            1)
                log INFO "Aborted by user."
                return "$SYNC_RC_RECOVERABLE"
                ;;
            2)
                backup_git_history || return "$SYNC_RC_UNSAFE"
                backup_worktree_collisions_for_ref "$remote_ref" true || return "$SYNC_RC_UNSAFE"
                capture_tracked_changes_manifest || return "$SYNC_RC_UNSAFE"
                backup_full_tracked_tree || return "$SYNC_RC_UNSAFE"
                backup_user_modifications || return "$SYNC_RC_UNSAFE"

                log INFO "Resetting to upstream..."
                if "${GIT_CMD[@]}" reset --hard "$remote_ref" >> "$LOG_FILE" 2>&1; then
                    log OK "Reset complete."
                    restore_user_modifications || return "$SYNC_RC_UNSAFE"
                    ensure_repo_defaults
                else
                    log ERROR "Reset failed."
                    return "$SYNC_RC_UNSAFE"
                fi
                ;;
            3)
                backup_git_history || return "$SYNC_RC_UNSAFE"
                backup_worktree_collisions_for_ref "$remote_ref" true || return "$SYNC_RC_UNSAFE"
                capture_tracked_changes_manifest || return "$SYNC_RC_UNSAFE"
                backup_full_tracked_tree || return "$SYNC_RC_UNSAFE"
                backup_user_modifications || return "$SYNC_RC_UNSAFE"

                "${GIT_CMD[@]}" reset --hard HEAD >> "$LOG_FILE" 2>&1 || true
                log INFO "Attempting rebase..."
                rebase_output="$("${GIT_CMD[@]}" rebase "$remote_ref" 2>&1)" || rebase_rc=$?
                printf '%s\n' "$rebase_output" >> "$LOG_FILE"

                if (( rebase_rc != 0 )); then
                    log ERROR "Rebase failed. Aborting and resetting..."
                    "${GIT_CMD[@]}" rebase --abort >> "$LOG_FILE" 2>&1 || true

                    if "${GIT_CMD[@]}" reset --hard "$remote_ref" >> "$LOG_FILE" 2>&1; then
                        log OK "Fallback reset complete."
                        restore_user_modifications || return "$SYNC_RC_UNSAFE"
                        ensure_repo_defaults
                    else
                        log ERROR "Reset also failed."
                        return "$SYNC_RC_UNSAFE"
                    fi
                else
                    log OK "Rebase successful."
                    restore_user_modifications || return "$SYNC_RC_UNSAFE"
                    ensure_repo_defaults
                fi
                ;;
            *)
                log INFO "Invalid choice. Aborting."
                return "$SYNC_RC_RECOVERABLE"
                ;;
        esac
    fi

    log OK "Repository synchronized."
    return 0
}

# ==============================================================================
# SUDO MANAGEMENT
# ==============================================================================
init_sudo() {
    if [[ -n "$SUDO_PID" ]] && kill -0 "$SUDO_PID" 2>/dev/null; then
        return 0
    fi

    log INFO "Acquiring sudo privileges for execution sequence..."
    sudo -v || { log ERROR "Sudo auth failed."; exit 1; }

    (
        if [[ -n "${LOCK_FD:-}" ]]; then
            exec {LOCK_FD}>&- 2>/dev/null || true
        fi

        trap 'exit 0' TERM

        while kill -0 "$MAIN_PID" 2>/dev/null; do
            sleep "$SUDO_KEEPALIVE_INTERVAL" &
            wait $! 2>/dev/null || true
            sudo -n -v 2>/dev/null || exit 0
        done
    ) &
    SUDO_PID=$!
}

stop_sudo() {
    if [[ -n "$SUDO_PID" ]] && kill -0 "$SUDO_PID" 2>/dev/null; then
        kill "$SUDO_PID" 2>/dev/null || true
        wait "$SUDO_PID" 2>/dev/null || true
    fi
    SUDO_PID=""
}

# ==============================================================================
# SCRIPT EXECUTION ENGINE
# ==============================================================================
execute_scripts() {
    log SECTION "Executing Update Sequence"

    local script_dir_missing=false
    if [[ ! -d "$SCRIPT_DIR" ]]; then
        script_dir_missing=true
        log WARN "Default script directory is missing: $SCRIPT_DIR"
    fi

    local i=0 total="${#MANIFEST_MODE[@]}"
    local mode="" script="" ignore_fail="" script_path="" is_custom=false
    local path_state="" quoted_args=""
    local preflight_failures=0
    local -a args=()

    for i in "${!MANIFEST_MODE[@]}"; do
        script="${MANIFEST_SCRIPT[$i]}"

        if [[ -v "CUSTOM_SCRIPT_PATHS[$script]" && -n "${CUSTOM_SCRIPT_PATHS[$script]}" ]]; then
            script_path="${WORK_TREE}/${CUSTOM_SCRIPT_PATHS[$script]}"
            is_custom=true
        else
            script_path="${SCRIPT_DIR}/${script}"
            is_custom=false
        fi

        MANIFEST_PATH[$i]="$script_path"
        MANIFEST_IS_CUSTOM[$i]="$is_custom"

        if [[ "$is_custom" != "true" && "$script_dir_missing" == "true" ]]; then
            MANIFEST_PATH_STATE[$i]="missing"
        elif [[ -e "$script_path" || -L "$script_path" ]]; then
            if [[ ! -f "$script_path" ]]; then
                MANIFEST_PATH_STATE[$i]="not-a-file"
            elif [[ ! -r "$script_path" ]]; then
                MANIFEST_PATH_STATE[$i]="unreadable"
            else
                MANIFEST_PATH_STATE[$i]="ok"
            fi
        else
            MANIFEST_PATH_STATE[$i]="missing"
        fi

        case "${MANIFEST_PATH_STATE[$i]}" in
            ok)
                ;;
            missing)
                log ERROR "Required script not found: $script -> $(quote_for_log "$script_path")"
                HARD_FAILED_SCRIPTS+=("$script (missing)")
                ((preflight_failures++)) || true
                ;;
            unreadable)
                log ERROR "Required script is unreadable: $script -> $(quote_for_log "$script_path")"
                HARD_FAILED_SCRIPTS+=("$script (unreadable)")
                ((preflight_failures++)) || true
                ;;
            not-a-file)
                log ERROR "Required script path is not a regular file: $script -> $(quote_for_log "$script_path")"
                HARD_FAILED_SCRIPTS+=("$script (not-a-file)")
                ((preflight_failures++)) || true
                ;;
        esac
    done

    if ((preflight_failures > 0)); then
        log ERROR "Aborting script execution due to ${preflight_failures} manifest path error(s)"
        return 0
    fi

    for i in "${!MANIFEST_MODE[@]}"; do
        mode="${MANIFEST_MODE[$i]}"
        script="${MANIFEST_SCRIPT[$i]}"
        ignore_fail="${MANIFEST_IGNORE_FAIL[$i]}"
        script_path="${MANIFEST_PATH[$i]}"
        path_state="${MANIFEST_PATH_STATE[$i]}"
        local -n argv_ref="${MANIFEST_ARGV_NAME[$i]}"
        args=("${argv_ref[@]}")
        quoted_args="$(join_quoted_argv "${args[@]}")"

        case "$path_state" in
            ok)
                ;;
            *)
                continue
                ;;
        esac

        if [[ "$mode" == "S" && -z "$SUDO_PID" && "$OPT_DRY_RUN" != true ]]; then
            init_sudo
        fi

        printf '%s[%d/%d]%s ' "$CLR_CYN" "$((i + 1))" "$total" "$CLR_RST"

        if [[ "$OPT_DRY_RUN" == true ]]; then
            if [[ -n "$quoted_args" ]]; then
                printf '%s→%s %s %s [DRY-RUN]\n' "$CLR_BLU" "$CLR_RST" "$script" "$quoted_args"
            else
                printf '%s→%s %s [DRY-RUN]\n' "$CLR_BLU" "$CLR_RST" "$script"
            fi
            continue
        fi

        if [[ -n "$quoted_args" ]]; then
            printf '%s→%s %s %s\n' "$CLR_BLU" "$CLR_RST" "$script" "$quoted_args"
        else
            printf '%s→%s %s\n' "$CLR_BLU" "$CLR_RST" "$script"
        fi

        local -i auto_retry_limit=3
        local -i auto_retry_count=0

        # The Retry/Skip prompt logic natively integrated into the execution sequence
        while true; do
            local rc=0
            case "$mode" in
                S) run_logged_command sudo "$BASH_BIN" "$script_path" "${args[@]}" || rc=$? ;;
                U) run_logged_command "$BASH_BIN" "$script_path" "${args[@]}" || rc=$? ;;
            esac

            if ((rc == 0)); then
                break
            fi

            if [[ "$ignore_fail" == "true" ]]; then
                log WARN "$script failed (exit $rc) - ignored via ignore-fail"
                SOFT_FAILED_SCRIPTS+=("$script")
                break
            fi

            if (( auto_retry_count < auto_retry_limit )); then
                (( ++auto_retry_count ))
                log WARN "$script failed (exit $rc). Auto-retrying (attempt ${auto_retry_count}/${auto_retry_limit})..."
                sleep 1
                continue
            fi

            log ERROR "$script failed (exit $rc)"

            if [[ -t 0 && "$OPT_FORCE" != true && "$OPT_DRY_RUN" != true ]]; then
                local _fail_choice=""
                printf '\n%s[ACTION REQUIRED]%s Script execution failed: %s\n' "$CLR_YLW" "$CLR_RST" "$script"
                read -r -p "Do you want to [S]kip, [R]etry, or [Q]uit? (s/r/q): " _fail_choice

                case "${_fail_choice,,}" in
                    s|skip)
                        log WARN "Skipping $script (User Selection)."
                        HARD_FAILED_SCRIPTS+=("$script (skipped by user)")
                        break
                        ;;
                    r|retry)
                        log INFO "Retrying $script..."
                        sleep 1
                        continue
                        ;;
                    *)
                        log ERROR "Stopping execution as requested."
                        HARD_FAILED_SCRIPTS+=("$script")
                        return 1
                        ;;
                esac
            else
                HARD_FAILED_SCRIPTS+=("$script")
                if [[ "$OPT_STOP_ON_FAIL" == true ]]; then
                    log ERROR "Stopping execution sequence due to --stop-on-fail"
                    return 1
                fi
                break
            fi
        done
    done

    return 0
}

# ==============================================================================
# SUMMARY & CLEANUP
# ==============================================================================
print_summary() {
    if [[ "$SKIP_FINAL_SUMMARY" == true || "$SUMMARY_PRINTED" == "true" ]]; then
        return 0
    fi
    SUMMARY_PRINTED=true

    printf '\n'
    log SECTION "Summary"

    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "Dry run complete — no changes were made."
    fi

    if [[ "$SYNC_FAILED" == true ]]; then
        log WARN "Sync phase did not complete successfully."
    fi

    if ((${#HARD_FAILED_SCRIPTS[@]} > 0)); then
            log ERROR "${#HARD_FAILED_SCRIPTS[@]} required script(s) failed:"
            local fs=""
            for fs in "${HARD_FAILED_SCRIPTS[@]}"; do
                log RAW "    • $fs"
            done
        elif [[ "$SYNC_FAILED" != true && ( "$CURRENT_PHASE" == "script execution" || "$CURRENT_PHASE" == "summary" || "$CURRENT_PHASE" == "cleanup" ) ]]; then
            log OK "All required operations completed successfully."
        fi

        if ((${#SOFT_FAILED_SCRIPTS[@]} > 0)); then
            log WARN "${#SOFT_FAILED_SCRIPTS[@]} script(s) soft failed (ignored):"
            local fs=""
            for fs in "${SOFT_FAILED_SCRIPTS[@]}"; do
                log RAW "    • $fs"
            done
        fi

        if ((${#SKIPPED_SCRIPTS[@]} > 0)); then
            log INFO "${#SKIPPED_SCRIPTS[@]} script(s) skipped:"
            local fs=""
            for fs in "${SKIPPED_SCRIPTS[@]}"; do
                log RAW "    • $fs"
            done
        fi

    if [[ -n "$LOG_FILE" ]]; then
        log INFO "Log saved to: $LOG_FILE"
    fi
}

cleanup() {
    local rc=$?
    CURRENT_PHASE="cleanup"

    stop_sudo
    release_lock

    if ((${#CREATED_TEMP_DIRS[@]} > 0)); then
        local tdir=""
        for tdir in "${CREATED_TEMP_DIRS[@]}"; do
            rm -rf -- "$tdir" 2>/dev/null || true
        done
    fi

    if [[ -n "$USER_MODS_BACKUP_DIR" && -d "$USER_MODS_BACKUP_DIR" ]]; then
        printf '\n'
        log WARN "Update was incomplete. Your modified files are preserved at:"
        printf '    %s\n' "$USER_MODS_BACKUP_DIR"
    fi

    if ((${#COLLISION_BACKUP_DIRS[@]} > 0)); then
        printf '\n'
        log INFO "Work-tree collision backups were preserved at:"
        local cdir=""
        for cdir in "${COLLISION_BACKUP_DIRS[@]}"; do
            [[ -d "$cdir" ]] && printf '    %s\n' "$cdir"
        done
    fi

    if [[ -n "$FULL_TRACKED_BACKUP_DIR" && -d "$FULL_TRACKED_BACKUP_DIR" ]]; then
        log INFO "Full tracked tree backup preserved at:"
        printf '    %s\n' "$FULL_TRACKED_BACKUP_DIR"
    fi

    if [[ -n "$GIT_HISTORY_BACKUP_DIR" && -d "$GIT_HISTORY_BACKUP_DIR" ]]; then
        log INFO "Git history backup preserved at:"
        printf '    %s\n' "$GIT_HISTORY_BACKUP_DIR"
    fi

    print_summary

    if ((${#HARD_FAILED_SCRIPTS[@]} > 0)); then
        desktop_notify critical "Dusky Update" "${#HARD_FAILED_SCRIPTS[@]} required script(s) failed"
        exit 1
    elif ((rc != 0)); then
        desktop_notify critical "Dusky Update" "Update failed or interrupted"
        exit "$rc"
    elif [[ "$SYNC_FAILED" == true ]]; then
        desktop_notify critical "Dusky Update" "Sync phase failed"
        exit 1
    else
        desktop_notify normal "Dusky Update" "Update completed successfully"
        exit 0
    fi
}

# ==============================================================================
# MAIN
# ==============================================================================
main() {
    CURRENT_PHASE="startup"
    parse_args "${ORIGINAL_ARGS[@]}"
    ensure_not_running_as_root
    check_dependencies

    if [[ -t 0 && "$OPT_FORCE" != true && "$OPT_POST_SELF_UPDATE" != true ]]; then
        printf '\n%sNote:%s Avoid interrupting the update while it'\''s running.\n' "${CLR_YLW}" "${CLR_RST}"
        printf 'Interruptions during git operations can leave the repository in a broken state.\n\n'
        local start_confirm=""
        read -r -p "Start the update? [y/N] " start_confirm
        if [[ ! "$start_confirm" =~ ^[Yy]$ ]]; then
            printf 'Update cancelled.\n'
            exit 0
        fi
    fi

    trap cleanup EXIT
    trap 'log WARN "Interrupted by user (SIGINT)"; exit 130' INT
    trap 'log WARN "Terminated (SIGTERM)"; exit 143' TERM
    trap 'log WARN "Hangup signal received (SIGHUP)"; exit 129' HUP

    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "Running in DRY-RUN mode — no changes will be made"
    else
        setup_storage_roots
        setup_runtime_dir
        setup_logging
        auto_prune
        acquire_lock || exit 1
    fi

    CURRENT_PHASE="preflight"
    parse_update_sequence_manifest
    require_sudo_if_needed || exit 1

    local self_hash_before=""
    if [[ "$OPT_DRY_RUN" != true && "$OPT_POST_SELF_UPDATE" != true && -r "$SELF_PATH" ]]; then
        self_hash_before="$(file_sha256 "$SELF_PATH" || true)"
    fi

    local cont="n"
    local sync_rc=0

    CURRENT_PHASE="sync"
    if [[ "$OPT_SKIP_SYNC" != true && "$OPT_POST_SELF_UPDATE" != true ]]; then
        if pull_updates; then
            if [[ "$OPT_DRY_RUN" != true && -n "$self_hash_before" && -r "$SELF_PATH" ]]; then
                local self_hash_after=""
                self_hash_after="$(file_sha256 "$SELF_PATH" || true)"
                if [[ -n "$self_hash_after" && "$self_hash_before" != "$self_hash_after" ]]; then
                    log SECTION "Self-Update Detected"
                    log OK "Reloading with updated script..."

                    CURRENT_PHASE="self-reexec"
                    SKIP_FINAL_SUMMARY=true
                    stop_sudo
                    release_lock

                    local -a reexec_args=("--post-self-update")
                    [[ "$OPT_DRY_RUN" == true ]] && reexec_args+=("--dry-run")
                    [[ "$OPT_FORCE" == true ]] && reexec_args+=("--force")
                    [[ "$OPT_SKIP_SYNC" == true ]] && reexec_args+=("--skip-sync")
                    [[ "$OPT_SYNC_ONLY" == true ]] && reexec_args+=("--sync-only")
                    [[ "$OPT_STOP_ON_FAIL" == true ]] && reexec_args+=("--stop-on-fail")
                    [[ "$OPT_ALLOW_DIVERGED_RESET" == true ]] && reexec_args+=("--allow-diverged-reset")

                    exec "$BASH_BIN" "$SELF_PATH" "${reexec_args[@]}"
                fi
            fi
        else
            sync_rc=$?
            SYNC_FAILED=true
            log WARN "Sync failed."

            if [[ "$OPT_SYNC_ONLY" == true ]]; then
                exit 1
            fi

            if ((sync_rc == SYNC_RC_RECOVERABLE)) && [[ -t 0 ]]; then
                read -r -t "$PROMPT_TIMEOUT_SHORT" -p "Continue with local scripts? [y/N] " cont || cont="n"
            else
                cont="n"
            fi

            [[ "$cont" =~ ^[Yy]$ ]] || exit 1
        fi
    fi

    if [[ "$OPT_SYNC_ONLY" == true ]]; then
        log OK "Sync-only mode — skipping script execution."
    elif [[ "$SYNC_FAILED" != true || "$cont" =~ ^[Yy]$ ]]; then
        CURRENT_PHASE="script execution"
        execute_scripts || true
    fi

    CURRENT_PHASE="summary"
}

main
