#!/usr/bin/env bash
# ==============================================================================
# DUSKY UPDATER (v9.0 forensic rewrite)
# Arch Linux / Hyprland dotfile and system updater.
# Bash 5.3+ only. Bare-repo workflow. Idempotent sync transaction model.
# ==============================================================================
set -Eeuo pipefail
shopt -s inherit_errexit extglob nullglob globstar lastpipe 2>/dev/null || true
shopt -s bash_source_fullpath 2>/dev/null || true

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
declare -r VERSION="9.0.0"
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
declare -r HOME_STATE_DIR="${HOME}/.local/state/dusky"
declare -r FALLBACK_LOG_BASE_DIR="${STATE_HOME_DIR}/logs"
declare -r FALLBACK_BACKUP_BASE_DIR="${HOME_STATE_DIR}/backups"
declare -r REPO_URL="https://github.com/dusklinux/dusky"
declare -r BRANCH="main"
declare -r UPSTREAM_REMOTE="dusky-upstream"
declare -r UPSTREAM_TRACKING_REF="refs/dusky-updater/upstream/${BRANCH}"

# ==============================================================================
# USER CONFIGURATION
# ==============================================================================
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
    "U | 410_waybar_swap_config.sh --toggle"
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
# STATIC RUNTIME
# ==============================================================================
declare -g GIT_BIN="" BASH_BIN=""
GIT_BIN="$(command -v git 2>/dev/null || true)"
BASH_BIN="$(command -v bash 2>/dev/null || true)"
[[ -n "$GIT_BIN" && -x "$GIT_BIN" ]] || { printf 'Error: git not found\n' >&2; exit 1; }
[[ -n "$BASH_BIN" && -x "$BASH_BIN" ]] || { printf 'Error: bash not found\n' >&2; exit 1; }
readonly GIT_BIN BASH_BIN

declare -gr MAIN_PID=$$
declare -g RUN_TIMESTAMP=""
printf -v RUN_TIMESTAMP '%(%Y%m%d_%H%M%S)T' -1
readonly RUN_TIMESTAMP

declare -g SELF_PATH="${BASH_SOURCE[0]:-$0}"
if [[ "$SELF_PATH" != /* ]]; then
    SELF_PATH="$(realpath -- "$SELF_PATH" 2>/dev/null || readlink -f -- "$SELF_PATH" 2>/dev/null || printf '%s' "$SELF_PATH")"
fi
readonly SELF_PATH

declare -gr CACHED_USER="${USER:-${LOGNAME:-unknown}}"
declare -ga ORIGINAL_ARGS=("$@")
declare -ga GIT_CMD=("$GIT_BIN" --git-dir="$DOTFILES_GIT_DIR" --work-tree="$WORK_TREE")
declare -ga GIT_LOCK_NAMES=(
    index.lock config.lock packed-refs.lock shallow.lock
    HEAD.lock ORIG_HEAD.lock FETCH_HEAD.lock
)

# ==============================================================================
# MUTABLE STATE
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
declare -g TRANSACTION_FILE=""

declare -g USER_MODS_BACKUP_DIR=""
declare -g FULL_TRACKED_BACKUP_DIR=""
declare -g GIT_HISTORY_BACKUP_DIR=""
declare -g MERGE_DIR=""

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
    declare -r CLR_RED="" CLR_GRN="" CLR_YLW="" CLR_BLU="" CLR_CYN="" CLR_RST=""
fi

# ==============================================================================
# BASIC HELPERS
# ==============================================================================
trim_ref() {
    local -n _trim_target="$1"
    _trim_target="${_trim_target##+([[:space:]])}"
    _trim_target="${_trim_target%%+([[:space:]])}"
}

path_exists() { [[ -e "$1" || -L "$1" ]]; }

path_parent() {
    local p="${1:-.}"
    case "$p" in
        /) REPLY=/ ;;
        */*) REPLY="${p%/*}"; [[ -n "$REPLY" ]] || REPLY=/ ;;
        *) REPLY=. ;;
    esac
}

path_base() { REPLY="${1##*/}"; }

nearest_existing_ancestor() {
    local p="${1:-.}"
    [[ -n "$p" ]] || p=.
    while [[ ! -e "$p" && ! -L "$p" ]]; do
        case "$p" in
            /|.) break ;;
            */*) p="${p%/*}"; [[ -n "$p" ]] || p=/ ;;
            *) p=. ;;
        esac
    done
    REPLY="$p"
}

quote_for_log() { printf '%q' "$1"; }

join_quoted_argv() {
    REPLY=""
    local arg qarg
    for arg in "$@"; do
        printf -v qarg '%q' "$arg"
        REPLY+="${REPLY:+ }${qarg}"
    done
}

log() {
    (($# >= 2)) || return 1
    local level="$1" msg="$2" prefix="" timestamp=""
    printf -v timestamp '%(%H:%M:%S)T' -1
    case "$level" in
        INFO)    prefix="${CLR_BLU}[INFO ]${CLR_RST}" ;;
        OK)      prefix="${CLR_GRN}[OK   ]${CLR_RST}" ;;
        WARN)    prefix="${CLR_YLW}[WARN ]${CLR_RST}" ;;
        ERROR)   prefix="${CLR_RED}[ERROR]${CLR_RST}" ;;
        SECTION) prefix=$'\n'"${CLR_CYN}=======${CLR_RST}" ;;
        RAW)     prefix="" ;;
        *)       prefix="[$level]" ;;
    esac
    if [[ "$level" == RAW ]]; then
        printf '%s\n' "$msg"
    else
        printf '%s %s\n' "$prefix" "$msg"
    fi
    if [[ -n "$LOG_FILE" && -w "$LOG_FILE" ]]; then
        printf '[%s] [%-7s] %s\n' "$timestamp" "$level" "$msg" >> "$LOG_FILE"
    fi
}

desktop_notify() {
    [[ "$OPT_DRY_RUN" == true ]] && return 0
    command -v notify-send >/dev/null 2>&1 || return 0
    timeout 3 notify-send --urgency="${1:-normal}" --app-name="Dusky Updater" \
        "${2:-Dusky Update}" "${3:-}" >/dev/null 2>&1 || true
}

show_help() {
    cat <<'HELPEOF'
Dusky Updater - Dotfile sync and setup tool for Arch Linux / Hyprland

Usage: dusky_updater.sh [OPTIONS]

Options:
  --help, -h               Show this help message and exit
  --version                Show version and exit
  --dry-run                Preview actions without making changes
  --skip-sync              Skip git sync, only run the script sequence
  --sync-only              Pull updates but do not run scripts
  --force                  Skip confirmation prompts
  --stop-on-fail           Kept for compatibility; hard failures already stop by default
  --allow-diverged-reset   In non-interactive mode, allow reset on diverged/unrelated history
  --list                   List active scripts in the update sequence

Update sequence entry formats:
  U | script.sh --auto
  S | ignore-fail | script.sh --auto
  U | | script.sh --auto
  U | true script.sh --auto     (legacy ignore-fail form)

Rules:
  - Field 1 is U or S.
  - Field 2 is optional flags; supported values: ignore-fail, ignore, true.
  - Command field is whitespace-split; quotes and backslashes are intentionally rejected.
HELPEOF
}

show_version() { printf 'Dusky Updater v%s\n' "$VERSION"; }

ensure_not_running_as_root() {
    if (( EUID == 0 )); then
        printf 'Error: Do not run this updater as root. Run as your user; S entries use sudo.\n' >&2
        exit 1
    fi
}

file_sha256() {
    local line=""
    line="$(sha256sum -- "$1" 2>/dev/null)" || return 1
    REPLY="${line%% *}"
}

# ==============================================================================
# MANIFEST PARSING
# ==============================================================================
parse_update_sequence_manifest() {
    local entry pipe_chars pipe_count f1 f2 f3 mode flags_part command_part script flag argv_name
    local -a parts=() flag_tokens=()
    local idx=0 ignore_fail=false

    MANIFEST_MODE=(); MANIFEST_SCRIPT=(); MANIFEST_IGNORE_FAIL=(); MANIFEST_ARGV_NAME=()
    MANIFEST_PATH=(); MANIFEST_PATH_STATE=(); MANIFEST_IS_CUSTOM=()

    for entry in "${UPDATE_SEQUENCE[@]}"; do
        [[ -z "${entry//[[:space:]]/}" ]] && continue
        pipe_chars="${entry//[^|]/}"
        pipe_count="${#pipe_chars}"
        (( pipe_count == 1 || pipe_count == 2 )) || {
            printf 'Error: UPDATE_SEQUENCE entry must contain exactly 1 or 2 pipe separators: %s\n' "$entry" >&2
            exit 1
        }

        f1= f2= f3=
        IFS='|' read -r f1 f2 f3 <<< "$entry"
        trim_ref f1; trim_ref f2; trim_ref f3
        mode="$f1"
        if (( pipe_count == 1 )); then
            flags_part=""
            command_part="$f2"
        else
            flags_part="$f2"
            command_part="$f3"
        fi

        [[ "$mode" == U || "$mode" == S ]] || {
            printf 'Error: Invalid UPDATE_SEQUENCE mode in entry: %s\n' "$entry" >&2
            exit 1
        }

        ignore_fail=false
        if [[ -n "$flags_part" ]]; then
            read -r -a flag_tokens <<< "${flags_part//,/ }"
            for flag in "${flag_tokens[@]}"; do
                case "$flag" in
                    true|ignore|ignore-fail) ignore_fail=true ;;
                    "") ;;
                    *)
                        printf 'Error: Unsupported flag %s in UPDATE_SEQUENCE entry: %s\n' "$flag" "$entry" >&2
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
                printf 'Error: Quotes and backslashes are forbidden in UPDATE_SEQUENCE command field: %s\n' "$entry" >&2
                exit 1
                ;;
        esac

        parts=()
        read -r -a parts <<< "$command_part"
        ((${#parts[@]} > 0)) || {
            printf 'Error: Missing script in UPDATE_SEQUENCE entry: %s\n' "$entry" >&2
            exit 1
        }

        if [[ "${parts[0]}" == true ]]; then
            ignore_fail=true
            parts=("${parts[@]:1}")
            ((${#parts[@]} > 0)) || {
                printf 'Error: Missing script after legacy true flag in entry: %s\n' "$entry" >&2
                exit 1
            }
        fi

        script="${parts[0]}"
        [[ -n "$script" ]] || {
            printf 'Error: Empty script name in UPDATE_SEQUENCE entry: %s\n' "$entry" >&2
            exit 1
        }

        argv_name="MANIFEST_ARGV_${idx}"
        declare -ga "$argv_name=()"
        local -n argv_ref="$argv_name"
        argv_ref=("${parts[@]:1}")

        MANIFEST_MODE+=("$mode")
        MANIFEST_SCRIPT+=("$script")
        MANIFEST_IGNORE_FAIL+=("$ignore_fail")
        MANIFEST_ARGV_NAME+=("$argv_name")
        MANIFEST_PATH+=("")
        MANIFEST_PATH_STATE+=(unknown)
        MANIFEST_IS_CUSTOM+=(false)
        ((idx++)) || true
    done
}

list_active_scripts() {
    ((${#MANIFEST_MODE[@]} > 0)) || parse_update_sequence_manifest
    local i display_mode script display_args
    printf 'Active scripts in update sequence:\n\n'
    for i in "${!MANIFEST_MODE[@]}"; do
        display_mode="${MANIFEST_MODE[$i]}"
        [[ "${MANIFEST_IGNORE_FAIL[$i]}" == true ]] && display_mode+=",ignore"
        script="${MANIFEST_SCRIPT[$i]}"
        local -n argv_ref="${MANIFEST_ARGV_NAME[$i]}"
        join_quoted_argv "${argv_ref[@]}"; display_args="$REPLY"
        printf '  %3d) [%s] %s' "$((i + 1))" "$display_mode" "$script"
        [[ -n "$display_args" ]] && printf ' %s' "$display_args"
        printf '\n'
    done
    printf '\nTotal: %d active script(s)\n' "${#MANIFEST_MODE[@]}"
}

parse_args() {
    while (($# > 0)); do
        case "$1" in
            --help|-h) show_help; exit 0 ;;
            --version) show_version; exit 0 ;;
            --dry-run) OPT_DRY_RUN=true ;;
            --skip-sync) OPT_SKIP_SYNC=true ;;
            --sync-only) OPT_SYNC_ONLY=true ;;
            --force) OPT_FORCE=true ;;
            --stop-on-fail) OPT_STOP_ON_FAIL=true ;;
            --allow-diverged-reset) OPT_ALLOW_DIVERGED_RESET=true ;;
            --list) list_active_scripts; exit 0 ;;
            --post-self-update) OPT_POST_SELF_UPDATE=true ;;
            -*) printf 'Unknown option: %s\nTry --help for usage.\n' "$1" >&2; exit 1 ;;
            *) printf 'Unexpected argument: %s\nTry --help for usage.\n' "$1" >&2; exit 1 ;;
        esac
        shift
    done
    if [[ "$OPT_SKIP_SYNC" == true && "$OPT_SYNC_ONLY" == true ]]; then
        printf 'Error: --skip-sync and --sync-only are mutually exclusive\n' >&2
        exit 1
    fi
}

require_sudo_if_needed() {
    local i
    [[ "$OPT_SYNC_ONLY" == true || "$OPT_DRY_RUN" == true ]] && return 0
    for i in "${!MANIFEST_MODE[@]}"; do
        if [[ "${MANIFEST_MODE[$i]}" == S ]]; then
            command -v sudo >/dev/null 2>&1 || { log ERROR 'sudo is required by UPDATE_SEQUENCE but is not installed'; return 1; }
            OPT_NEEDS_SUDO=true
            return 0
        fi
    done
}

# ==============================================================================
# STORAGE / RUNTIME
# ==============================================================================
check_dependencies() {
    local -a missing=()
    local cmd
    for cmd in flock sha256sum timeout mktemp find stat du cp mv rm chmod mkdir tee; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    if ((${#missing[@]})); then
        printf 'Error: Missing required commands: %s\n' "${missing[*]}" >&2
        printf 'Install with: sudo pacman -S bash coreutils findutils git util-linux\n' >&2
        exit 1
    fi
}

ensure_secure_runtime_dir() {
    local dir="$1"
    [[ -L "$dir" || ( -e "$dir" && ! -d "$dir" ) ]] && return 1
    [[ -d "$dir" ]] || mkdir -p -- "$dir" || return 1
    chmod 700 -- "$dir" 2>/dev/null || true
    [[ -d "$dir" && ! -L "$dir" && -O "$dir" && -w "$dir" ]]
}

ensure_storage_dir() {
    local dir="$1"
    [[ -L "$dir" || ( -e "$dir" && ! -d "$dir" ) ]] && return 1
    [[ -d "$dir" ]] || mkdir -p -- "$dir" || return 1
    chmod 700 -- "$dir" 2>/dev/null || true
    [[ -d "$dir" && ! -L "$dir" && -O "$dir" && -w "$dir" ]]
}

path_device_id() {
    REPLY="$(stat -c '%d' -- "$1" 2>/dev/null || true)"
    [[ "$REPLY" =~ ^[0-9]+$ ]]
}

same_device() {
    local da db
    path_device_id "$1" || return 1; da="$REPLY"
    path_device_id "$2" || return 1; db="$REPLY"
    [[ "$da" == "$db" ]]
}

choose_storage_dir() {
    local preferred="$1" fallback="$2"
    local -n out_ref="$3"
    if ensure_storage_dir "$preferred"; then out_ref="$preferred"; return 0; fi
    if ensure_storage_dir "$fallback"; then out_ref="$fallback"; return 0; fi
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
    ensure_secure_runtime_dir "$candidate" || { printf 'Error: Cannot create runtime directory: %s\n' "$candidate" >&2; exit 1; }
    RUNTIME_DIR="$candidate"
    LOCK_FILE="${candidate}/lock"
}

setup_storage_roots() {
    choose_storage_dir "$LOG_BASE_DIR" "$FALLBACK_LOG_BASE_DIR" ACTIVE_LOG_BASE_DIR || {
        printf 'Error: Cannot create any usable log directory\n' >&2
        exit 1
    }

    local candidate
    for candidate in "$BACKUP_BASE_DIR" "$FALLBACK_BACKUP_BASE_DIR" "${HOME_STATE_DIR}/backups"; do
        if ensure_storage_dir "$candidate" && same_device "$WORK_TREE" "$candidate"; then
            ACTIVE_BACKUP_BASE_DIR="$candidate"
            break
        fi
    done
    [[ -n "$ACTIVE_BACKUP_BASE_DIR" ]] || {
        printf 'Error: Cannot create a backup directory on the same filesystem as %s\n' "$WORK_TREE" >&2
        exit 1
    }

    ensure_storage_dir "$STATE_HOME_DIR" || ensure_storage_dir "$HOME_STATE_DIR" || {
        printf 'Error: Cannot create state directory\n' >&2
        exit 1
    }
    if [[ -d "$STATE_HOME_DIR" && -w "$STATE_HOME_DIR" ]]; then
        TRANSACTION_FILE="${STATE_HOME_DIR}/pending_sync.bash"
    else
        TRANSACTION_FILE="${HOME_STATE_DIR}/pending_sync.bash"
    fi
}

make_private_dir_under() {
    local base="$1" template="$2" dir=""
    ensure_storage_dir "$base" || return 1
    dir="$(mktemp -d -p "$base" "$template")" || return 1
    chmod 700 -- "$dir" 2>/dev/null || true
    printf '%s' "$dir"
}

make_private_file_under() {
    local base="$1" template="$2" file=""
    ensure_storage_dir "$base" || return 1
    file="$(mktemp -p "$base" "$template")" || return 1
    chmod 600 -- "$file" 2>/dev/null || true
    printf '%s' "$file"
}

setup_logging() {
    LOG_FILE="$(make_private_file_under "$ACTIVE_LOG_BASE_DIR" "dusky_update_${RUN_TIMESTAMP}_XXXXXX.log")" || {
        printf 'Error: Cannot create log file\n' >&2
        exit 1
    }
    {
        printf '================================================================================\n'
        printf ' DUSKY UPDATE LOG - %s\n' "$RUN_TIMESTAMP"
        printf ' Kernel: %s | User: %s | Bash: %s\n' "$(uname -r)" "$CACHED_USER" "$BASH_VERSION"
        printf '================================================================================\n'
    } >> "$LOG_FILE"
}

fs_free_bytes() {
    local blocks=0 bsize=0
    if read -r blocks bsize < <(stat -f -c '%a %S' -- "$1" 2>/dev/null); then
        [[ "$blocks" =~ ^[0-9]+$ && "$bsize" =~ ^[0-9]+$ ]] || { printf '0'; return 1; }
        printf '%s' "$((blocks * bsize))"
        return 0
    fi
    printf '0'
    return 1
}

check_disk_space() {
    local path="$1" available_bytes=0 min_bytes=$((DISK_MIN_FREE_MB * 1024 * 1024))
    available_bytes="$(fs_free_bytes "$path")"
    if (( available_bytes < min_bytes )); then
        log ERROR "Low disk space: $((available_bytes / 1048576))MB available at $path; need ${DISK_MIN_FREE_MB}MB"
        return 1
    fi
}

path_copy_size_bytes() {
    local path="$1" line="" size=0
    if ! path_exists "$path"; then printf '0'; return 0; fi
    line="$(du -sB1 -- "$path" 2>/dev/null || true)"
    size="${line%%[[:space:]]*}"
    [[ "$size" =~ ^[0-9]+$ ]] || size=0
    printf '%s' "$size"
}

ensure_free_space_for_bytes() {
    local target_path="$1" required_bytes="$2" context="${3:-operation}"
    local reserve_bytes=$((DISK_COPY_RESERVE_MB * 1024 * 1024))
    local available_bytes=0 required_total=0
    (( required_bytes > 0 )) || return 0
    available_bytes="$(fs_free_bytes "$target_path")"
    required_total=$((required_bytes + reserve_bytes))
    if (( available_bytes < required_total )); then
        log ERROR "Insufficient free space for ${context}: $(((available_bytes + 1048575) / 1048576))MB available, need $(((required_total + 1048575) / 1048576))MB"
        return 1
    fi
}

ensure_relative_parent_dir() {
    local root="$1" rel="$2"
    local -n cache_ref="$3"
    local parent_abs="$root"
    if [[ "$rel" == */* ]]; then
        parent_abs="${root}/${rel%/*}"
    fi
    [[ -n "${cache_ref[$parent_abs]:-}" ]] && return 0
    mkdir -p -- "$parent_abs" || return 1
    cache_ref["$parent_abs"]=1
}

copy_path_atomic() {
    local src="$1" dest="$2" parent base tmpdir tmp
    path_parent "$dest"; parent="$REPLY"
    path_base "$dest"; base="$REPLY"
    mkdir -p -- "$parent" || return 1
    tmpdir="$(mktemp -d -p "$parent" ".${base}.dusky_copy.XXXXXX")" || return 1
    CREATED_TEMP_DIRS+=("$tmpdir")
    tmp="${tmpdir}/${base}"
    cp -a --reflink=auto -- "$src" "$tmp" || return 1
    mv -T -- "$tmp" "$dest" || return 1
    rm -rf -- "$tmpdir" 2>/dev/null || true
}

auto_prune() {
    [[ -d "$ACTIVE_LOG_BASE_DIR" ]] && find "$ACTIVE_LOG_BASE_DIR" -type f -name 'dusky_update_*.log' -mtime "+${LOG_RETENTION_DAYS}" -delete 2>/dev/null || true
    [[ -d "$ACTIVE_BACKUP_BASE_DIR" ]] && find "$ACTIVE_BACKUP_BASE_DIR" -mindepth 1 -maxdepth 1 -type d \
        \( -name 'pre_reset_*' -o -name 'user_mods_*' -o -name 'untracked_collisions_*' -o -name 'needs_merge_*' -o -name 'initial_conflicts_*' -o -name 'repo_history_*' -o -name 'interrupted_current_*' \) \
        -mtime "+${BACKUP_RETENTION_DAYS}" -exec rm -rf {} + 2>/dev/null || true
}

# ==============================================================================
# LOCKING
# ==============================================================================
fd_holders_for_path() {
    local path="$1" fd pid cmdline
    REPLY=""
    declare -A _seen=()
    for fd in /proc/[0-9]*/fd/*; do
        [[ -e "$fd" ]] || continue
        [[ "$fd" -ef "$path" ]] || continue
        pid="${fd#/proc/}"; pid="${pid%%/*}"
        [[ "$pid" == "$$" || -n "${_seen[$pid]:-}" ]] && continue
        _seen[$pid]=1
        cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
        cmdline="${cmdline% }"
        REPLY+="    -> PID ${pid}: ${cmdline:-[unknown]}"$'\n'
    done
}

acquire_lock() {
    exec {LOCK_FD}>>"$LOCK_FILE" || { log ERROR "Cannot open lock file: $LOCK_FILE"; return 1; }
    if ! flock -n "$LOCK_FD"; then
        log ERROR 'Another Dusky Updater instance is already running.'
        fd_holders_for_path "$LOCK_FILE"
        if [[ -n "$REPLY" ]]; then
            log WARN 'Processes currently holding the updater lock:'
            log RAW "${REPLY%$'\n'}"
        fi
        exec {LOCK_FD}>&- 2>/dev/null || true
        LOCK_FD=""
        return 1
    fi
}

release_lock() {
    if [[ -n "$LOCK_FD" ]]; then
        exec {LOCK_FD}>&- 2>/dev/null || true
        LOCK_FD=""
    fi
}

# ==============================================================================
# GIT HELPERS
# ==============================================================================
detect_git_lock_state() {
    local lock path
    for lock in "${GIT_LOCK_NAMES[@]}"; do
        path="${DOTFILES_GIT_DIR}/${lock}"
        if [[ -e "$path" ]]; then REPLY="$lock"; return 0; fi
    done
    for path in "${DOTFILES_GIT_DIR}"/refs/**/*.lock; do
        [[ -e "$path" ]] || continue
        REPLY="${path#${DOTFILES_GIT_DIR}/}"
        return 0
    done
    REPLY=none
}

clear_stale_git_locks() {
    local lock_state lock_file current_time mtime lock_age prompt_ans can_delete stat_before stat_after
    detect_git_lock_state; lock_state="$REPLY"
    while [[ "$lock_state" != none ]]; do
        lock_file="${DOTFILES_GIT_DIR}/${lock_state}"
        can_delete=false
        log WARN "Git lock detected: $lock_file"

        current_time="$EPOCHSECONDS"
        mtime="$(stat -c %Y -- "$lock_file" 2>/dev/null || printf '%s' "$current_time")"
        (( lock_age = current_time - mtime )) || lock_age=0

        fd_holders_for_path "$lock_file"
        if [[ -n "$REPLY" ]]; then
            log ERROR 'Git lock is open by a live process. Refusing to remove it.'
            log RAW "${REPLY%$'\n'}"
            return 1
        fi

        (( lock_age > 60 )) && can_delete=true
        if [[ "$OPT_DRY_RUN" == true ]]; then
            log WARN "[DRY-RUN] Would evaluate stale Git lock: $lock_file"
            return 1
        elif [[ -t 0 && "$OPT_FORCE" != true ]]; then
            printf '\n%s[GIT LOCK]%s %s is not open by any process.\n' "$CLR_YLW" "$CLR_RST" "$lock_file"
            read -r -t "$PROMPT_TIMEOUT_SHORT" -p 'Clear it and continue? [y/N] ' prompt_ans || prompt_ans=n
            [[ "$prompt_ans" =~ ^[Yy]$ ]] && can_delete=true
        elif [[ "$can_delete" != true ]]; then
            log ERROR "Git lock is too recent (${lock_age}s) for unattended removal."
            return 1
        fi

        [[ "$can_delete" == true ]] || { log ERROR 'Git lock removal declined.'; return 1; }
        stat_before="$(stat -Lc '%d:%i:%Y' -- "$lock_file" 2>/dev/null || true)"
        fd_holders_for_path "$lock_file"
        [[ -z "$REPLY" ]] || { log ERROR 'Git lock became active while checking it.'; return 1; }
        rm -f -- "$lock_file" 2>/dev/null || true
        stat_after="$(stat -Lc '%d:%i:%Y' -- "$lock_file" 2>/dev/null || true)"
        if [[ -n "$stat_after" && "$stat_after" == "$stat_before" ]]; then
            log ERROR "Failed to remove Git lock: $lock_file"
            return 1
        fi
        detect_git_lock_state; lock_state="$REPLY"
    done
    log OK 'No active Git locks remain.'
}

get_repo_state() {
    if [[ -L "$DOTFILES_GIT_DIR" ]]; then log ERROR "Git dir must not be a symlink: $DOTFILES_GIT_DIR"; REPLY=invalid; return 0; fi
    if [[ ! -e "$DOTFILES_GIT_DIR" ]]; then REPLY=absent; return 0; fi
    if [[ ! -d "$DOTFILES_GIT_DIR" ]]; then log ERROR "Git dir path is not a directory: $DOTFILES_GIT_DIR"; REPLY=invalid; return 0; fi
    if [[ ! -O "$DOTFILES_GIT_DIR" ]]; then log ERROR "Git dir is not owned by current user: $DOTFILES_GIT_DIR"; REPLY=invalid; return 0; fi
    if [[ ! -d "$WORK_TREE" || ! -w "$WORK_TREE" ]]; then log ERROR "Work tree is not writable: $WORK_TREE"; REPLY=invalid; return 0; fi
    clear_stale_git_locks || { REPLY=invalid; return 0; }
    if ! "${GIT_CMD[@]}" rev-parse --git-dir >/dev/null 2>&1; then
        log ERROR "Repository metadata is invalid or corrupted: $DOTFILES_GIT_DIR"
        REPLY=invalid
        return 0
    fi
    REPLY=valid
}

ensure_repo_defaults() {
    local value=""
    value="$("${GIT_CMD[@]}" config --get status.showUntrackedFiles 2>/dev/null || true)"
    if [[ "$value" != no ]]; then
        [[ "$OPT_DRY_RUN" == true ]] && log INFO '[DRY-RUN] Would set status.showUntrackedFiles=no' || "${GIT_CMD[@]}" config status.showUntrackedFiles no >/dev/null 2>&1 || true
    fi
}

detect_git_operation_state() {
    if [[ -d "${DOTFILES_GIT_DIR}/rebase-merge" || -d "${DOTFILES_GIT_DIR}/rebase-apply" ]]; then REPLY=rebase
    elif [[ -f "${DOTFILES_GIT_DIR}/MERGE_HEAD" ]]; then REPLY=merge
    elif [[ -f "${DOTFILES_GIT_DIR}/CHERRY_PICK_HEAD" ]]; then REPLY=cherry-pick
    elif [[ -f "${DOTFILES_GIT_DIR}/REVERT_HEAD" ]]; then REPLY=revert
    elif [[ -f "${DOTFILES_GIT_DIR}/BISECT_LOG" ]]; then REPLY=bisect
    else REPLY=none
    fi
}

normalize_git_state() {
    detect_git_operation_state
    case "$REPLY" in
        none) return 0 ;;
        rebase|merge|cherry-pick|revert) log ERROR "Git $REPLY is in progress. Resolve it manually first."; return 1 ;;
        bisect) log ERROR "Git bisect is in progress. Run git bisect reset first."; return 1 ;;
        *) log ERROR "Unknown Git operation state: $REPLY"; return 1 ;;
    esac
}

canonicalize_git_remote_url() {
    local url="${1-}"
    url="${url%/}"; url="${url%.git}"
    case "$url" in
        git@github.com:*) REPLY="github.com/${url#git@github.com:}" ;;
        ssh://git@github.com/*) REPLY="github.com/${url#ssh://git@github.com/}" ;;
        https://github.com/*) REPLY="github.com/${url#https://github.com/}" ;;
        http://github.com/*) REPLY="github.com/${url#http://github.com/}" ;;
        *) REPLY="$url" ;;
    esac
}

get_upstream_fetch_source() {
    local expected current remote
    canonicalize_git_remote_url "$REPO_URL"; expected="$REPLY"
    for remote in origin "$UPSTREAM_REMOTE"; do
        current="$("${GIT_CMD[@]}" remote get-url "$remote" 2>/dev/null || true)"
        [[ -n "$current" ]] || continue
        canonicalize_git_remote_url "$current"
        if [[ "$REPLY" == "$expected" ]]; then REPLY="$remote"; return 0; fi
    done
    current="$("${GIT_CMD[@]}" remote get-url "$UPSTREAM_REMOTE" 2>/dev/null || true)"
    [[ -n "$current" ]] && log WARN "Existing ${UPSTREAM_REMOTE} remote points elsewhere; fetching direct URL."
    REPLY="$REPO_URL"
}

fetch_with_retry() {
    local source="${1:?missing source}" attempt=1 wait_time=$FETCH_INITIAL_BACKOFF rc=0
    while (( attempt <= FETCH_MAX_ATTEMPTS )); do
        if timeout "${FETCH_TIMEOUT}s" "${GIT_CMD[@]}" fetch --no-tags --prune --no-write-fetch-head "$source" \
            "+refs/heads/${BRANCH}:${UPSTREAM_TRACKING_REF}" >> "$LOG_FILE" 2>&1; then
            return 0
        fi
        rc=$?
        if (( attempt < FETCH_MAX_ATTEMPTS )); then
            (( rc == 124 )) && log WARN "Fetch attempt ${attempt}/${FETCH_MAX_ATTEMPTS} timed out; retrying in ${wait_time}s" || log WARN "Fetch attempt ${attempt}/${FETCH_MAX_ATTEMPTS} failed; retrying in ${wait_time}s"
            sleep "$wait_time"
            (( wait_time *= 2, attempt++ ))
        else
            (( attempt++ ))
        fi
    done
    (( rc == 124 )) && log ERROR "Fetch failed after ${FETCH_MAX_ATTEMPTS} attempts due to timeouts" || log ERROR "Fetch failed after ${FETCH_MAX_ATTEMPTS} attempts"
    return 1
}

clone_with_retry() {
    local attempt=1 wait_time=$FETCH_INITIAL_BACKOFF rc=0 parent tmp
    path_parent "$DOTFILES_GIT_DIR"; parent="$REPLY"
    while (( attempt <= FETCH_MAX_ATTEMPTS )); do
        tmp="$(mktemp -d -p "$parent" ".dusky.clone.XXXXXX")" || return 1
        CREATED_TEMP_DIRS+=("$tmp")
        rmdir -- "$tmp" || return 1
        if timeout "${CLONE_TIMEOUT}s" "$GIT_BIN" clone --bare --branch "$BRANCH" "$REPO_URL" "$tmp" >> "$LOG_FILE" 2>&1; then
            if mv -T -- "$tmp" "$DOTFILES_GIT_DIR"; then
                return 0
            fi
            log ERROR "Clone succeeded but atomic install failed: $DOTFILES_GIT_DIR"
            rm -rf -- "$tmp" 2>/dev/null || true
            return 1
        fi
        rc=$?
        rm -rf -- "$tmp" 2>/dev/null || true
        if (( attempt < FETCH_MAX_ATTEMPTS )); then
            (( rc == 124 )) && log WARN "Clone attempt ${attempt}/${FETCH_MAX_ATTEMPTS} timed out; retrying in ${wait_time}s" || log WARN "Clone attempt ${attempt}/${FETCH_MAX_ATTEMPTS} failed; retrying in ${wait_time}s"
            sleep "$wait_time"
            (( wait_time *= 2, attempt++ ))
        else
            (( attempt++ ))
        fi
    done
    (( rc == 124 )) && log ERROR "Clone failed after ${FETCH_MAX_ATTEMPTS} attempts due to timeouts" || log ERROR "Clone failed after ${FETCH_MAX_ATTEMPTS} attempts"
    return 1
}

show_update_preview() {
    local local_head="$1" remote_head="$2" base_commit="${3:-}" diff_base commit_count='?'
    local -a changed_files=()
    diff_base="${base_commit:-$local_head}"
    commit_count="$("${GIT_CMD[@]}" rev-list --count "${local_head}..${remote_head}" 2>/dev/null || printf '?')"
    mapfile -d '' -t changed_files < <("${GIT_CMD[@]}" diff -z --name-only "${diff_base}..${remote_head}" 2>/dev/null || true)
    printf '\n'
    log INFO 'Upstream changes:'
    printf '    Commits behind:  %s\n' "$commit_count"
    printf '    Files changed:   %d\n' "${#changed_files[@]}"
    if [[ "$commit_count" != '?' ]] && (( commit_count > 0 )); then
        printf '\n    Recent commits:\n'
        "${GIT_CMD[@]}" log --oneline --no-decorate -10 "${local_head}..${remote_head}" 2>/dev/null | while IFS= read -r line; do printf '      %s\n' "$line"; done || true
        (( commit_count > 10 )) && printf '      ... and %d more\n' "$((commit_count - 10))"
    fi
    printf '\n'
}

git_head_path_meta() {
    local path="$1" record meta mode type oid
    if IFS= read -r -d '' record < <("${GIT_CMD[@]}" ls-tree -z HEAD -- "$path" 2>/dev/null); then
        meta="${record%%$'\t'*}"
        read -r mode type oid <<< "$meta"
        REPLY="${mode}"$'\t'"${oid}"
    else
        REPLY=""
    fi
}

# ==============================================================================
# COLLISION / BACKUP / RESTORE
# ==============================================================================
collect_dir_collision_roots() {
    local root_rel="$1" tracked_exact_name="$2" tracked_desc_name="$3" out_name="$4"
    local -n tracked_exact_ref="$tracked_exact_name" tracked_desc_ref="$tracked_desc_name" out_ref="$out_name"
    local rel abs child_abs child last_idx skip
    local -a stack=()
    [[ -d "${WORK_TREE}/${root_rel}" && ! -L "${WORK_TREE}/${root_rel}" ]] || return 0
    stack+=("$root_rel")
    while ((${#stack[@]})); do
        last_idx=$((${#stack[@]} - 1)); rel="${stack[$last_idx]}"; unset "stack[$last_idx]"
        abs="${WORK_TREE}/${rel}"
        path_exists "$abs" || continue
        if [[ -L "$abs" || ! -d "$abs" ]]; then
            [[ -z "${tracked_exact_ref[$rel]+_}" ]] && out_ref["$rel"]=1
            continue
        fi
        if [[ -n "${tracked_exact_ref[$rel]+_}" ]]; then
            out_ref["$rel"]=1
            continue
        fi
        skip=true
        for child_abs in "$abs"/* "$abs"/.[!.]* "$abs"/..?*; do
            [[ -e "$child_abs" || -L "$child_abs" ]] || continue
            skip=false
            child="${child_abs##*/}"
            if [[ -n "${tracked_desc_ref[$rel]+_}" ]]; then
                stack+=("${rel}/${child}")
            else
                out_ref["$rel"]=1
                break
            fi
        done
        [[ "$skip" == true && -n "${tracked_desc_ref[$rel]+_}" ]] && out_ref["$rel"]=1
    done
}

backup_worktree_collisions_for_ref() {
    local ref="$1" honor_current_tracked="${2:-true}"
    local target_path abs ancestor remaining part tracked_path coll_rel coll_src coll_dest coll_backup_dir coll_manifest info_file
    local required_bytes=0 path_bytes=0 skip=false
    local -A collision_candidates=() collision_roots=() mkdir_cache=() current_tracked_exact=() current_tracked_descendants=()

    if [[ "$honor_current_tracked" == true ]]; then
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
                if [[ "$honor_current_tracked" == true && -n "${current_tracked_descendants[$target_path]+_}" ]]; then
                    collect_dir_collision_roots "$target_path" current_tracked_exact current_tracked_descendants collision_candidates
                else
                    collision_candidates["$target_path"]=1
                fi
            elif [[ "$honor_current_tracked" != true || -z "${current_tracked_exact[$target_path]+_}" ]]; then
                collision_candidates["$target_path"]=1
            fi
        fi
        ancestor=""; remaining="$target_path"
        while [[ "$remaining" == */* ]]; do
            part="${remaining%%/*}"
            [[ -z "$ancestor" ]] && ancestor="$part" || ancestor+="/${part}"
            abs="${WORK_TREE}/${ancestor}"
            if path_exists "$abs" && { [[ -L "$abs" || ! -d "$abs" ]]; }; then
                [[ "$honor_current_tracked" != true || -z "${current_tracked_exact[$ancestor]+_}" ]] && collision_candidates["$ancestor"]=1
                break
            fi
            remaining="${remaining#*/}"
        done
    done < <("${GIT_CMD[@]}" ls-tree -r -z --name-only "$ref" 2>/dev/null)

    for coll_rel in "${!collision_candidates[@]}"; do
        skip=false; ancestor="$coll_rel"
        while [[ "$ancestor" == */* ]]; do
            ancestor="${ancestor%/*}"
            if [[ -n "${collision_candidates[$ancestor]+_}" ]]; then skip=true; break; fi
        done
        [[ "$skip" == true ]] || collision_roots["$coll_rel"]=1
    done
    ((${#collision_roots[@]})) || return 0

    for coll_rel in "${!collision_roots[@]}"; do
        coll_src="${WORK_TREE}/${coll_rel}"
        path_exists "$coll_src" || continue
        path_bytes="$(path_copy_size_bytes "$coll_src")"
        (( required_bytes += path_bytes ))
    done
    check_disk_space "$ACTIVE_BACKUP_BASE_DIR" || return 1
    ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$required_bytes" 'collision backup' || return 1

    coll_backup_dir="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "untracked_collisions_${RUN_TIMESTAMP}_XXXXXX")" || { log ERROR 'Failed to create collision backup directory'; return 1; }
    COLLISION_BACKUP_DIRS+=("$coll_backup_dir")
    coll_manifest="${coll_backup_dir}/MOVED_PATHS.txt"
    info_file="${coll_backup_dir}/INFO.txt"
    : > "$coll_manifest" || return 1
    chmod 600 -- "$coll_manifest" 2>/dev/null || true
    {
        printf 'Dusky work-tree collision backup\nCreated: %s\nReference: %s\nWork tree: %s\n' "$RUN_TIMESTAMP" "$ref" "$WORK_TREE"
    } > "$info_file" || return 1
    chmod 600 -- "$info_file" 2>/dev/null || true

    log WARN "Found ${#collision_roots[@]} work-tree collision(s). Moving them atomically to backup..."
    for coll_rel in "${!collision_roots[@]}"; do
        coll_src="${WORK_TREE}/${coll_rel}"
        path_exists "$coll_src" || continue
        coll_dest="${coll_backup_dir}/${coll_rel}"
        ensure_relative_parent_dir "$coll_backup_dir" "$coll_rel" mkdir_cache || { log ERROR "Failed to create backup parent for $(quote_for_log "$coll_rel")"; return 1; }
        mv -T -- "$coll_src" "$coll_dest" || { log ERROR "Failed to move collision: $(quote_for_log "$coll_rel")"; return 1; }
        COLLISION_MOVED_PATHS["$coll_rel"]=1
        printf '%q\n' "$coll_rel" >> "$coll_manifest" || return 1
        log RAW "  -> Backed up collision: $(quote_for_log "$coll_rel")"
    done
    log OK "Collisions backed up to: $coll_backup_dir"
}

capture_tracked_changes_manifest() {
    local -a raw_records=()
    local meta path oldmode newmode oldoid newoid status
    local i=0 count=0 parsed_count=0 quiet_rc=0
    CHANGE_PATHS=(); CHANGE_STATUS=(); CHANGE_OLD_MODE=(); CHANGE_OLD_OID=(); CHANGE_BACKUP_HAS_FILE=()
    "${GIT_CMD[@]}" update-index -q --refresh >/dev/null 2>&1 || true
    if "${GIT_CMD[@]}" diff-index --quiet --ignore-submodules HEAD -- >/dev/null 2>&1; then
        return 0
    else
        quiet_rc=$?
        if (( quiet_rc != 1 )); then
            log ERROR "git diff-index failed while capturing tracked changes (rc=${quiet_rc})."
            return 1
        fi
    fi
    mapfile -d '' -t raw_records < <("${GIT_CMD[@]}" diff-index --raw --no-renames -z HEAD -- 2>/dev/null || true)
    count="${#raw_records[@]}"
    while (( i < count )); do
        meta="${raw_records[i]}"; path="${raw_records[i+1]:-}"; (( i += 2 ))
        [[ -n "$meta" && -n "$path" ]] || continue
        read -r oldmode newmode oldoid newoid status <<< "${meta#:}"
        status="${status%%[0-9]*}"
        CHANGE_PATHS+=("$path")
        CHANGE_STATUS["$path"]="$status"
        CHANGE_OLD_MODE["$path"]="$oldmode"
        CHANGE_OLD_OID["$path"]="$oldoid"
        CHANGE_BACKUP_HAS_FILE["$path"]=0
        (( parsed_count++ )) || true
    done
    if (( count > 1 && parsed_count == 0 )); then
        log ERROR 'Git reported tracked changes, but the raw diff parser produced no paths.'
        return 1
    fi
}

backup_user_modifications() {
    local backup_dir manifest_file path status src dest qpath path_bytes required_bytes=0 copied_count=0
    local -A mkdir_cache=()
    [[ -n "$USER_MODS_BACKUP_DIR" && -d "$USER_MODS_BACKUP_DIR" ]] && return 0
    ((${#CHANGE_PATHS[@]})) || return 0

    for path in "${CHANGE_PATHS[@]}"; do
        status="${CHANGE_STATUS[$path]:-?}"; src="${WORK_TREE}/${path}"
        if [[ "$status" == D || ! ( -e "$src" || -L "$src" ) ]]; then
            continue
        fi
        path_bytes="$(path_copy_size_bytes "$src")"
        (( required_bytes += path_bytes ))
    done
    check_disk_space "$ACTIVE_BACKUP_BASE_DIR" || return 1
    ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$required_bytes" 'modified-files backup' || return 1

    backup_dir="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "user_mods_${RUN_TIMESTAMP}_XXXXXX")" || { log ERROR 'Failed to create modified-files backup directory'; return 1; }
    manifest_file="${backup_dir}/MANIFEST.txt"
    : > "$manifest_file" || return 1
    chmod 600 -- "$manifest_file" 2>/dev/null || true
    USER_MODS_BACKUP_DIR="$backup_dir"

    for path in "${CHANGE_PATHS[@]}"; do
        status="${CHANGE_STATUS[$path]:-?}"; src="${WORK_TREE}/${path}"
        printf -v qpath '%q' "$path"
        if [[ "$status" == D || ! ( -e "$src" || -L "$src" ) ]]; then
            CHANGE_STATUS["$path"]=D
            CHANGE_BACKUP_HAS_FILE["$path"]=0
            printf 'status=%s old_oid=%s has_copy=0 path=%s\n' D "${CHANGE_OLD_OID[$path]:-}" "$qpath" >> "$manifest_file"
            continue
        fi
        dest="${backup_dir}/${path}"
        ensure_relative_parent_dir "$backup_dir" "$path" mkdir_cache || { log ERROR "Failed to create backup parent for $(quote_for_log "$path")"; return 1; }
        copy_path_atomic "$src" "$dest" || { log ERROR "Failed to back up modified path: $(quote_for_log "$path")"; return 1; }
        CHANGE_BACKUP_HAS_FILE["$path"]=1
        printf 'status=%s old_oid=%s has_copy=1 path=%s\n' "$status" "${CHANGE_OLD_OID[$path]:-}" "$qpath" >> "$manifest_file"
        (( copied_count++ )) || true
    done
    log OK "Backed up ${#CHANGE_PATHS[@]} tracked change(s) to: $backup_dir"
    (( copied_count == 0 )) && log INFO 'Tracked changes were deletion-only; deletion intent is preserved in the manifest.'
    return 0
}

backup_full_tracked_tree() {
    local template="${1:-pre_reset_${RUN_TIMESTAMP}_XXXXXX}" backup_dir info_file path src dest path_bytes required_bytes=0 copied_count=0
    local -a tracked_paths=()
    local -A mkdir_cache=()
    [[ -n "$FULL_TRACKED_BACKUP_DIR" && -d "$FULL_TRACKED_BACKUP_DIR" ]] && return 0
    mapfile -d '' -t tracked_paths < <("${GIT_CMD[@]}" ls-files -z 2>/dev/null)
    for path in "${tracked_paths[@]}"; do
        src="${WORK_TREE}/${path}"
        path_exists "$src" || continue
        path_bytes="$(path_copy_size_bytes "$src")"
        (( required_bytes += path_bytes ))
    done
    check_disk_space "$ACTIVE_BACKUP_BASE_DIR" || return 1
    ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$required_bytes" 'full tracked-tree backup' || return 1
    backup_dir="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "$template")" || { log ERROR 'Failed to create full tracked-tree backup'; return 1; }
    info_file="${backup_dir}/INFO.txt"
    {
        printf 'Dusky full tracked-tree backup\nCreated: %s\nHEAD: %s\n' "$RUN_TIMESTAMP" "$("${GIT_CMD[@]}" rev-parse HEAD 2>/dev/null || printf unknown)"
    } > "$info_file" || true
    chmod 600 -- "$info_file" 2>/dev/null || true
    for path in "${tracked_paths[@]}"; do
        src="${WORK_TREE}/${path}"
        path_exists "$src" || continue
        dest="${backup_dir}/${path}"
        ensure_relative_parent_dir "$backup_dir" "$path" mkdir_cache || return 1
        copy_path_atomic "$src" "$dest" || { log ERROR "Failed to back up tracked path: $(quote_for_log "$path")"; return 1; }
        (( copied_count++ )) || true
    done
    FULL_TRACKED_BACKUP_DIR="$backup_dir"
    log OK "Full tracked-tree backup preserved at: $backup_dir ($copied_count path(s))"
}

backup_git_history() {
    local backup_root backup_repo info_file required_bytes=0
    [[ -n "$GIT_HISTORY_BACKUP_DIR" && -d "$GIT_HISTORY_BACKUP_DIR" ]] && return 0
    required_bytes="$(path_copy_size_bytes "$DOTFILES_GIT_DIR")"
    check_disk_space "$ACTIVE_BACKUP_BASE_DIR" || return 1
    ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$required_bytes" 'Git history backup' || return 1
    backup_root="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "repo_history_${RUN_TIMESTAMP}_XXXXXX")" || { log ERROR 'Failed to create Git history backup root'; return 1; }
    backup_repo="${backup_root}/repo.git"
    cp -a --reflink=auto -- "$DOTFILES_GIT_DIR" "$backup_repo" || { log ERROR 'Failed to preserve Git history'; return 1; }
    info_file="${backup_root}/INFO.txt"
    printf 'Dusky Git history backup\nCreated: %s\nSource: %s\n' "$RUN_TIMESTAMP" "$DOTFILES_GIT_DIR" > "$info_file" || true
    chmod 600 -- "$info_file" 2>/dev/null || true
    GIT_HISTORY_BACKUP_DIR="$backup_root"
    log OK "Git history backup preserved at: $backup_root"
}

ensure_merge_dir() {
    [[ -n "$MERGE_DIR" && -d "$MERGE_DIR" ]] && return 0
    MERGE_DIR="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "needs_merge_${RUN_TIMESTAMP}_XXXXXX")" || { log ERROR 'Failed to create manual merge directory'; return 1; }
}

path_has_collision_backup() {
    local path="$1" moved_path
    [[ -n "${COLLISION_MOVED_PATHS[$path]+_}" ]] && return 0
    for moved_path in "${!COLLISION_MOVED_PATHS[@]}"; do
        [[ "$moved_path" == "$path/"* ]] && return 0
    done
    return 1
}

classify_restore_action() {
    local path="$1" status="$2" old_mode="$3" old_oid="$4"
    local head_meta new_mode="" new_oid="" action old_oid_valid=false safe_restore=false
    git_head_path_meta "$path"; head_meta="$REPLY"
    [[ -n "$head_meta" ]] && IFS=$'\t' read -r new_mode new_oid <<< "$head_meta"
    [[ -n "$old_oid" && "$old_oid" != 0000000000000000000000000000000000000000 ]] && old_oid_valid=true

    if [[ "$status" == D ]]; then
        if path_has_collision_backup "$path"; then action=delete-merge
        elif [[ -z "$new_oid" ]]; then action=delete-preserved
        elif [[ "$old_oid_valid" == true && "$new_oid" == "$old_oid" && "$new_mode" == "$old_mode" ]]; then action=delete-safe
        else action=delete-merge
        fi
        REPLY="${action}"$'\t'"${new_mode}"$'\t'"${new_oid}"
        return 0
    fi

    if [[ "$old_oid_valid" == true ]]; then
        [[ -n "$new_oid" && "$new_oid" == "$old_oid" && "$new_mode" == "$old_mode" ]] && safe_restore=true
    else
        [[ -z "$new_oid" ]] && safe_restore=true
    fi
    [[ "$safe_restore" == true ]] && action=restore || action=merge
    REPLY="${action}"$'\t'"${new_mode}"$'\t'"${new_oid}"
}

atomic_restore_path() {
    local src="$1" target="$2" parent base new_dir new_path old_dir old_path restore_mode=replace rc=0 copy_bytes=0 probe_path
    path_parent "$target"; parent="$REPLY"
    path_base "$target"; base="$REPLY"
    mkdir -p -- "$parent" || return 1
    copy_bytes="$(path_copy_size_bytes "$src")"
    if (( copy_bytes > 0 )); then
        nearest_existing_ancestor "$parent"; probe_path="$REPLY"
        ensure_free_space_for_bytes "$probe_path" "$copy_bytes" "restoring $(quote_for_log "$target")" || return 1
    fi
    new_dir="$(mktemp -d -p "$parent" ".${base}.dusky_new.XXXXXX")" || return 1
    CREATED_TEMP_DIRS+=("$new_dir")
    new_path="${new_dir}/${base}"
    cp -a --reflink=auto -- "$src" "$new_path" || return 1

    if path_exists "$target" && { [[ -d "$target" && ! -L "$target" ]] || [[ -d "$src" && ! -L "$src" ]]; }; then
        restore_mode=two-phase
    fi

    if [[ "$restore_mode" == replace ]]; then
        mv -fT -- "$new_path" "$target" || return 1
        rm -rf -- "$new_dir" 2>/dev/null || true
        return 0
    fi

    old_dir="$(mktemp -d -p "$parent" ".${base}.dusky_old.XXXXXX")" || return 1
    old_path="${old_dir}/${base}"
    if path_exists "$target"; then
        mv -T -- "$target" "$old_path" || { rmdir -- "$old_dir" 2>/dev/null || true; return 1; }
    fi
    if mv -T -- "$new_path" "$target"; then
        rm -rf -- "$new_dir" "$old_dir" 2>/dev/null || true
        return 0
    fi
    rc=$?
    if [[ ! -e "$target" && ! -L "$target" && ( -e "$old_path" || -L "$old_path" ) ]]; then
        mv -T -- "$old_path" "$target" 2>/dev/null || log ERROR "Rollback failed; old target preserved at: $old_path"
    elif [[ -e "$old_path" || -L "$old_path" ]]; then
        log WARN "Old target preserved for manual recovery at: $old_path"
    fi
    return "$rc"
}

restore_user_modifications() {
    local path status old_mode old_oid backup_src target plan action new_mode new_oid merge_dest marker
    local probe_path device_id backup_bytes target_bytes cumulative_delta peak_required current_required merge_required_bytes=0
    local restored_count=0 merge_count=0 deletion_count=0 all_ok=true
    local -A mkdir_cache=() restore_device_probe=() restore_device_peak=() restore_device_delta=() planned_action=() planned_new_mode=() planned_new_oid=()

    [[ -n "$USER_MODS_BACKUP_DIR" && -d "$USER_MODS_BACKUP_DIR" ]] || return 0
    ((${#CHANGE_PATHS[@]})) || return 0

    for path in "${CHANGE_PATHS[@]}"; do
        status="${CHANGE_STATUS[$path]:-?}"; old_mode="${CHANGE_OLD_MODE[$path]:-}"; old_oid="${CHANGE_OLD_OID[$path]:-}"
        backup_src="${USER_MODS_BACKUP_DIR}/${path}"; target="${WORK_TREE}/${path}"
        if [[ "$status" != D ]]; then
            [[ "${CHANGE_BACKUP_HAS_FILE[$path]:-0}" == 1 ]] || continue
            path_exists "$backup_src" || continue
        fi
        classify_restore_action "$path" "$status" "$old_mode" "$old_oid"; plan="$REPLY"
        IFS=$'\t' read -r action new_mode new_oid <<< "$plan"
        planned_action["$path"]="$action"; planned_new_mode["$path"]="$new_mode"; planned_new_oid["$path"]="$new_oid"
        case "$action" in
            restore)
                backup_bytes="$(path_copy_size_bytes "$backup_src")"
                target_bytes=0; path_exists "$target" && target_bytes="$(path_copy_size_bytes "$target")"
                path_parent "$target"; nearest_existing_ancestor "$REPLY"; probe_path="$REPLY"
                path_device_id "$probe_path" || { log ERROR "Cannot determine filesystem for restore target: $(quote_for_log "$path")"; return 1; }
                device_id="$REPLY"
                cumulative_delta="${restore_device_delta[$device_id]:-0}"
                peak_required="${restore_device_peak[$device_id]:-0}"
                current_required=$((cumulative_delta + backup_bytes))
                (( current_required > peak_required )) && restore_device_peak["$device_id"]=$current_required
                restore_device_delta["$device_id"]=$((cumulative_delta + backup_bytes - target_bytes))
                [[ -z "${restore_device_probe[$device_id]:-}" ]] && restore_device_probe["$device_id"]="$probe_path"
                ;;
            merge)
                backup_bytes="$(path_copy_size_bytes "$backup_src")"
                (( merge_required_bytes += backup_bytes ))
                ;;
        esac
    done

    for device_id in "${!restore_device_peak[@]}"; do
        peak_required="${restore_device_peak[$device_id]:-0}"
        (( peak_required > 0 )) || continue
        ensure_free_space_for_bytes "${restore_device_probe[$device_id]}" "$peak_required" 'tracked-change restoration' || return 1
    done
    if (( merge_required_bytes > 0 )); then
        ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$merge_required_bytes" 'manual-merge copies' || return 1
    fi

    log INFO 'Restoring tracked user changes...'
    for path in "${CHANGE_PATHS[@]}"; do
        status="${CHANGE_STATUS[$path]:-?}"; old_mode="${CHANGE_OLD_MODE[$path]:-}"; old_oid="${CHANGE_OLD_OID[$path]:-}"
        backup_src="${USER_MODS_BACKUP_DIR}/${path}"; target="${WORK_TREE}/${path}"
        action="${planned_action[$path]:-}"; new_mode="${planned_new_mode[$path]:-}"; new_oid="${planned_new_oid[$path]:-}"
        [[ -n "$action" ]] || continue
        case "$action" in
            delete-preserved)
                (( deletion_count++ )) || true
                ;;
            delete-safe)
                rm -rf -- "$target" || { log ERROR "Failed to re-apply deletion: $(quote_for_log "$path")"; all_ok=false; continue; }
                (( deletion_count++ )) || true
                ;;
            delete-merge)
                ensure_merge_dir || { all_ok=false; continue; }
                marker="${MERGE_DIR}/${path}.dusky_deleted"
                ensure_relative_parent_dir "$MERGE_DIR" "${path}.dusky_deleted" mkdir_cache || { all_ok=false; continue; }
                {
                    printf 'Tracked deletion requires manual review.\nPath: %q\nOld mode: %s\nOld oid: %s\nCurrent mode: %s\nCurrent oid: %s\n' \
                        "$path" "$old_mode" "$old_oid" "${new_mode:-<absent>}" "${new_oid:-<absent>}"
                } > "$marker" || { all_ok=false; continue; }
                chmod 600 -- "$marker" 2>/dev/null || true
                (( merge_count++ )) || true
                log RAW "  -> Manual review needed for deletion: $(quote_for_log "$path")"
                ;;
            restore)
                if atomic_restore_path "$backup_src" "$target"; then
                    (( restored_count++ )) || true
                    log RAW "  -> Restored: $(quote_for_log "$path")"
                else
                    log ERROR "Failed to restore: $(quote_for_log "$path")"
                    all_ok=false
                fi
                ;;
            merge)
                ensure_merge_dir || { all_ok=false; continue; }
                merge_dest="${MERGE_DIR}/${path}"
                ensure_relative_parent_dir "$MERGE_DIR" "$path" mkdir_cache || { all_ok=false; continue; }
                copy_path_atomic "$backup_src" "$merge_dest" || { log ERROR "Failed to save merge copy: $(quote_for_log "$path")"; all_ok=false; continue; }
                (( merge_count++ )) || true
                log RAW "  -> Upstream changed too: $(quote_for_log "$path") (your version saved for merge)"
                ;;
            *)
                log ERROR "Unknown restore action for: $(quote_for_log "$path")"
                all_ok=false
                ;;
        esac
    done

    (( restored_count > 0 )) && log OK "Auto-restored $restored_count path(s)."
    if (( merge_count > 0 )); then log WARN "$merge_count path(s) require manual merge."; log INFO "Review: $MERGE_DIR"; fi
    (( deletion_count > 0 )) && log WARN "$deletion_count tracked deletion(s) preserved or queued for manual review."
    (( restored_count == 0 && merge_count == 0 && deletion_count == 0 )) && log INFO 'No modifications needed restoration.'

    if [[ "$all_ok" == true ]]; then
        rm -rf -- "$USER_MODS_BACKUP_DIR" 2>/dev/null || true
        USER_MODS_BACKUP_DIR=""
        return 0
    fi
    log ERROR "Some paths could not be restored. Backup preserved at: $USER_MODS_BACKUP_DIR"
    return 1
}

# ==============================================================================
# SYNC TRANSACTION LEDGER
# ==============================================================================
begin_sync_transaction() {
    local target_oid="$1" tmp="${TRANSACTION_FILE}.${MAIN_PID}.tmp"
    [[ "$OPT_DRY_RUN" == true || -z "$TRANSACTION_FILE" ]] && return 0
    mkdir -p -- "${TRANSACTION_FILE%/*}" || return 1
    {
        printf 'DUSKY_TRANSACTION_VERSION=1\n'
        printf 'TRANS_TARGET_OID=%q\n' "$target_oid"
        printf 'USER_MODS_BACKUP_DIR=%q\n' "$USER_MODS_BACKUP_DIR"
        declare -p CHANGE_PATHS CHANGE_STATUS CHANGE_OLD_MODE CHANGE_OLD_OID CHANGE_BACKUP_HAS_FILE COLLISION_MOVED_PATHS 2>/dev/null
    } > "$tmp" || return 1
    chmod 600 -- "$tmp" 2>/dev/null || true
    mv -fT -- "$tmp" "$TRANSACTION_FILE"
}

clear_sync_transaction() {
    [[ -n "$TRANSACTION_FILE" ]] && rm -f -- "$TRANSACTION_FILE" "${TRANSACTION_FILE}.${MAIN_PID}.tmp" 2>/dev/null || true
}

recover_pending_transaction() {
    local target=""
    [[ -n "$TRANSACTION_FILE" && -f "$TRANSACTION_FILE" ]] || return 0
    log WARN "Pending sync transaction detected: $TRANSACTION_FILE"
    # shellcheck source=/dev/null
    source "$TRANSACTION_FILE" || { log ERROR 'Pending transaction state is unreadable.'; return "$SYNC_RC_UNSAFE"; }
    target="${TRANS_TARGET_OID:-}"
    if [[ -z "$target" ]]; then
        log WARN 'Pending transaction has no target commit; preserving any backups and clearing stale state.'
        clear_sync_transaction
        return 0
    fi
    "${GIT_CMD[@]}" rev-parse --verify -q "${target}^{commit}" >/dev/null 2>&1 || {
        log ERROR "Pending transaction target commit is unavailable: $target"
        return "$SYNC_RC_RECOVERABLE"
    }
    log INFO 'Backing up current tracked tree before transaction recovery...'
    FULL_TRACKED_BACKUP_DIR=""
    backup_full_tracked_tree "interrupted_current_${RUN_TIMESTAMP}_XXXXXX" || return "$SYNC_RC_UNSAFE"
    log INFO "Completing interrupted reset to ${target}..."
    "${GIT_CMD[@]}" reset --hard "$target" >> "$LOG_FILE" 2>&1 || return "$SYNC_RC_UNSAFE"
    if [[ -n "$USER_MODS_BACKUP_DIR" && -d "$USER_MODS_BACKUP_DIR" ]]; then
        restore_user_modifications || return "$SYNC_RC_UNSAFE"
    fi
    clear_sync_transaction
    log OK 'Pending sync transaction recovered.'
}

# ==============================================================================
# INITIAL CLONE / HISTORY MODES
# ==============================================================================
initial_clone() {
    log SECTION 'First-Time Setup'
    log INFO "Bare repository not found at: $DOTFILES_GIT_DIR"
    local do_clone=y head_oid=""
    [[ -d "$WORK_TREE" && -w "$WORK_TREE" ]] || { log ERROR "Work tree is not writable: $WORK_TREE"; return "$SYNC_RC_UNSAFE"; }
    if [[ -t 0 && "$OPT_FORCE" != true ]]; then
        printf '\n'
        read -r -t "$PROMPT_TIMEOUT_LONG" -p "Clone from ${REPO_URL}? [y/N] " do_clone || do_clone=n
        do_clone="${do_clone:-n}"
    fi
    [[ "$do_clone" =~ ^[Yy]$ ]] || { log INFO 'Clone cancelled.'; return "$SYNC_RC_RECOVERABLE"; }
    if [[ "$OPT_DRY_RUN" == true ]]; then log INFO "[DRY-RUN] Would clone ${BRANCH}: $REPO_URL -> $DOTFILES_GIT_DIR"; return 0; fi
    log INFO 'Cloning bare repository into atomic staging directory...'
    clone_with_retry || return "$SYNC_RC_UNSAFE"
    ensure_repo_defaults
    backup_worktree_collisions_for_ref HEAD false || return "$SYNC_RC_UNSAFE"
    CHANGE_PATHS=(); CHANGE_STATUS=(); CHANGE_OLD_MODE=(); CHANGE_OLD_OID=(); CHANGE_BACKUP_HAS_FILE=(); COLLISION_MOVED_PATHS=()
    head_oid="$("${GIT_CMD[@]}" rev-parse HEAD 2>/dev/null || true)"
    begin_sync_transaction "$head_oid" || return "$SYNC_RC_UNSAFE"
    log INFO 'Checking out files...'
    if "${GIT_CMD[@]}" checkout -f >> "$LOG_FILE" 2>&1; then
        clear_sync_transaction
        log OK 'Repository cloned and checked out successfully.'
        return 0
    fi
    log ERROR 'Checkout failed. Recovery state was preserved for the next run.'
    return "$SYNC_RC_UNSAFE"
}

initialize_unborn_repo_from_ref() {
    local remote_ref="$1" target_oid=""
    if [[ "$OPT_DRY_RUN" == true ]]; then log INFO "[DRY-RUN] Would initialize unborn repository from ${remote_ref}"; return 0; fi
    "${GIT_CMD[@]}" symbolic-ref HEAD "refs/heads/${BRANCH}" >> "$LOG_FILE" 2>&1 || { log ERROR "Failed to point HEAD at refs/heads/${BRANCH}"; return 1; }
    backup_worktree_collisions_for_ref "$remote_ref" false || return 1
    CHANGE_PATHS=(); CHANGE_STATUS=(); CHANGE_OLD_MODE=(); CHANGE_OLD_OID=(); CHANGE_BACKUP_HAS_FILE=()
    target_oid="$("${GIT_CMD[@]}" rev-parse "$remote_ref" 2>/dev/null || true)"
    begin_sync_transaction "$target_oid" || return 1
    if "${GIT_CMD[@]}" reset --hard "$remote_ref" >> "$LOG_FILE" 2>&1; then
        clear_sync_transaction
        log OK 'Initialized existing empty repository from upstream.'
        return 0
    fi
    log ERROR 'Failed to initialize unborn repository from upstream.'
    return 1
}

handle_unrelated_upstream_history() {
    local remote_ref="$1" sync_choice=1
    log WARN "Local repository does not share history with ${remote_ref}."
    if [[ "$OPT_DRY_RUN" == true ]]; then log INFO "[DRY-RUN] Would back up tracked tree/history and reset to ${remote_ref}"; return 0; fi
    if [[ -t 0 ]]; then
        printf '\n%s[UNRELATED HISTORY]%s Existing repo is not based on Dusky upstream.\n' "$CLR_YLW" "$CLR_RST"
        printf '  1) Abort [DEFAULT]\n  %s2) Replace local repo contents with upstream%s\n' "$CLR_GRN" "$CLR_RST"
        read -r -t "$PROMPT_TIMEOUT_LONG" -p 'Choice [1-2] (default: 1): ' sync_choice 2>/dev/null || sync_choice=1
    elif [[ "$OPT_ALLOW_DIVERGED_RESET" == true ]]; then sync_choice=2
    else log ERROR 'Non-interactive unrelated history; use --allow-diverged-reset to override.'; return "$SYNC_RC_RECOVERABLE"
    fi
    sync_choice="${sync_choice:-1}"
    case "$sync_choice" in
        1) log INFO 'Aborted by user.'; return "$SYNC_RC_RECOVERABLE" ;;
        2)
            backup_git_history || return "$SYNC_RC_UNSAFE"
            backup_worktree_collisions_for_ref "$remote_ref" true || return "$SYNC_RC_UNSAFE"
            backup_full_tracked_tree || return "$SYNC_RC_UNSAFE"
            log INFO "Resetting to ${remote_ref}..."
            "${GIT_CMD[@]}" reset --hard "$remote_ref" >> "$LOG_FILE" 2>&1 || { log ERROR 'Reset failed.'; return "$SYNC_RC_UNSAFE"; }
            log OK 'Reset complete. Previous tracked tree/history were preserved.'
            ;;
        *) log INFO 'Invalid choice. Aborting.'; return "$SYNC_RC_RECOVERABLE" ;;
    esac
}

# ==============================================================================
# PULL UPDATES
# ==============================================================================
pull_updates() {
    log SECTION 'Synchronizing Dotfiles Repository'
    local repo_state fetch_source remote_ref="$UPSTREAM_TRACKING_REF" local_head remote_head base_commit sync_choice=1 rebase_output rebase_rc=0 mb_rc=0

    get_repo_state; repo_state="$REPLY"
    case "$repo_state" in
        absent) initial_clone && { log OK 'Repository synchronized (initial clone).'; return 0; }; return $? ;;
        invalid) return "$SYNC_RC_UNSAFE" ;;
        valid) ;;
        *) log ERROR "Unknown repository state: $repo_state"; return "$SYNC_RC_UNSAFE" ;;
    esac

    normalize_git_state || return "$SYNC_RC_UNSAFE"
    recover_pending_transaction || return $?
    get_upstream_fetch_source || return "$SYNC_RC_UNSAFE"; fetch_source="$REPLY"

    log INFO 'Fetching from upstream...'
    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "[DRY-RUN] Would fetch branch ${BRANCH} from ${fetch_source}"
    else
        fetch_with_retry "$fetch_source" || return "$SYNC_RC_RECOVERABLE"
        log OK 'Fetch complete.'
    fi

    local_head="$("${GIT_CMD[@]}" rev-parse --verify -q HEAD 2>/dev/null || true)"
    remote_head="$("${GIT_CMD[@]}" rev-parse --verify -q "$remote_ref" 2>/dev/null || true)"
    if [[ -z "$remote_head" ]]; then
        [[ "$OPT_DRY_RUN" == true ]] && { log WARN '[DRY-RUN] No cached upstream ref found.'; return 0; }
        log ERROR "Cannot determine upstream HEAD for ${BRANCH}"
        return "$SYNC_RC_UNSAFE"
    fi
    if [[ -z "$local_head" ]]; then
        [[ "$OPT_DRY_RUN" == true ]] && { log INFO "[DRY-RUN] Would initialize unborn HEAD from ${remote_ref}"; return 0; }
        initialize_unborn_repo_from_ref "$remote_ref" || return "$SYNC_RC_UNSAFE"
        ensure_repo_defaults
        log OK 'Repository synchronized.'
        return 0
    fi

    if [[ "$local_head" == "$remote_head" ]]; then
        local unhealthy=0 changed_path changed_status
        capture_tracked_changes_manifest || return "$SYNC_RC_UNSAFE"
        for changed_path in "${CHANGE_PATHS[@]}"; do
            changed_status="${CHANGE_STATUS[$changed_path]:-}"
            [[ "$changed_status" == D || "$changed_status" == T ]] && (( unhealthy++ )) || true
        done
        (( unhealthy > 0 )) && log WARN "HEAD matches upstream, but ${unhealthy} tracked path(s) are missing/type-mismatched; leaving as user changes." || log OK 'Already up to date.'
        [[ "$OPT_DRY_RUN" == true ]] || ensure_repo_defaults
        return 0
    fi

    base_commit="$("${GIT_CMD[@]}" merge-base "$local_head" "$remote_head" 2>/dev/null)" || mb_rc=$?
    if (( mb_rc == 1 )) || [[ "$mb_rc" -eq 0 && -z "$base_commit" ]]; then
        handle_unrelated_upstream_history "$remote_ref" || return $?
        [[ "$OPT_DRY_RUN" == true ]] || ensure_repo_defaults
        [[ "$OPT_DRY_RUN" == true ]] || log OK 'Repository synchronized.'
        return 0
    elif (( mb_rc != 0 )); then
        log ERROR "Cannot determine merge-base with upstream (git rc=$mb_rc)."
        return "$SYNC_RC_UNSAFE"
    fi

    show_update_preview "$local_head" "$remote_head" "$base_commit"
    if [[ "$base_commit" == "$local_head" ]]; then
        log INFO 'Fast-forwarding to upstream...'
        [[ "$OPT_DRY_RUN" == true ]] && { log INFO "[DRY-RUN] Would reset --hard to ${remote_ref}"; return 0; }
        backup_worktree_collisions_for_ref "$remote_ref" true || return "$SYNC_RC_UNSAFE"
        capture_tracked_changes_manifest || return "$SYNC_RC_UNSAFE"
        backup_user_modifications || { log ERROR 'Backup failed; aborting update.'; return "$SYNC_RC_UNSAFE"; }
        begin_sync_transaction "$remote_head" || return "$SYNC_RC_UNSAFE"
        if "${GIT_CMD[@]}" reset --hard "$remote_ref" >> "$LOG_FILE" 2>&1; then
            log OK 'Updated to latest.'
            restore_user_modifications || return "$SYNC_RC_UNSAFE"
            clear_sync_transaction
            ensure_repo_defaults
        else
            log ERROR 'Reset failed. Recovery state preserved for next run.'
            return "$SYNC_RC_UNSAFE"
        fi
    else
        log WARN 'Local history diverged from upstream.'
        [[ "$OPT_DRY_RUN" == true ]] && { log INFO "[DRY-RUN] Would require reset or rebase to ${remote_ref}"; return 0; }
        if [[ -t 0 ]]; then
            printf '\n%s[DIVERGED HISTORY]%s Choose sync method:\n' "$CLR_YLW" "$CLR_RST"
            printf '  1) Abort [DEFAULT]\n  %s2) Reset to upstream [RECOMMENDED]%s\n  3) Attempt rebase\n\n' "$CLR_GRN" "$CLR_RST"
            read -r -t "$PROMPT_TIMEOUT_LONG" -p 'Choice [1-3] (default: 1): ' sync_choice 2>/dev/null || sync_choice=1
        elif [[ "$OPT_ALLOW_DIVERGED_RESET" == true ]]; then sync_choice=2
        else log ERROR 'Non-interactive diverged history; use --allow-diverged-reset to override.'; return "$SYNC_RC_RECOVERABLE"
        fi
        sync_choice="${sync_choice:-1}"
        case "$sync_choice" in
            1) log INFO 'Aborted by user.'; return "$SYNC_RC_RECOVERABLE" ;;
            2)
                backup_git_history || return "$SYNC_RC_UNSAFE"
                backup_worktree_collisions_for_ref "$remote_ref" true || return "$SYNC_RC_UNSAFE"
                capture_tracked_changes_manifest || return "$SYNC_RC_UNSAFE"
                backup_full_tracked_tree || return "$SYNC_RC_UNSAFE"
                backup_user_modifications || return "$SYNC_RC_UNSAFE"
                begin_sync_transaction "$remote_head" || return "$SYNC_RC_UNSAFE"
                log INFO 'Resetting to upstream...'
                "${GIT_CMD[@]}" reset --hard "$remote_ref" >> "$LOG_FILE" 2>&1 || { log ERROR 'Reset failed.'; return "$SYNC_RC_UNSAFE"; }
                restore_user_modifications || return "$SYNC_RC_UNSAFE"
                clear_sync_transaction
                ensure_repo_defaults
                ;;
            3)
                backup_git_history || return "$SYNC_RC_UNSAFE"
                backup_worktree_collisions_for_ref "$remote_ref" true || return "$SYNC_RC_UNSAFE"
                capture_tracked_changes_manifest || return "$SYNC_RC_UNSAFE"
                backup_full_tracked_tree || return "$SYNC_RC_UNSAFE"
                backup_user_modifications || return "$SYNC_RC_UNSAFE"
                begin_sync_transaction "$remote_head" || return "$SYNC_RC_UNSAFE"
                "${GIT_CMD[@]}" reset --hard HEAD >> "$LOG_FILE" 2>&1 || true
                log INFO 'Attempting rebase...'
                rebase_output="$("${GIT_CMD[@]}" rebase "$remote_ref" 2>&1)" || rebase_rc=$?
                printf '%s\n' "$rebase_output" >> "$LOG_FILE"
                if (( rebase_rc != 0 )); then
                    log ERROR 'Rebase failed; aborting and resetting to upstream.'
                    "${GIT_CMD[@]}" rebase --abort >> "$LOG_FILE" 2>&1 || true
                    "${GIT_CMD[@]}" reset --hard "$remote_ref" >> "$LOG_FILE" 2>&1 || { log ERROR 'Fallback reset failed.'; return "$SYNC_RC_UNSAFE"; }
                fi
                restore_user_modifications || return "$SYNC_RC_UNSAFE"
                clear_sync_transaction
                ensure_repo_defaults
                ;;
            *) log INFO 'Invalid choice. Aborting.'; return "$SYNC_RC_RECOVERABLE" ;;
        esac
    fi
    log OK 'Repository synchronized.'
}

# ==============================================================================
# SUDO AND SCRIPT EXECUTION
# ==============================================================================
init_sudo() {
    if [[ -n "$SUDO_PID" ]] && kill -0 "$SUDO_PID" 2>/dev/null; then return 0; fi
    log INFO 'Acquiring sudo privileges for execution sequence...'
    sudo -v || { log ERROR 'Sudo authentication failed.'; return 1; }
    (
        [[ -n "${LOCK_FD:-}" ]] && exec {LOCK_FD}>&- 2>/dev/null || true
        trap 'exit 0' TERM INT HUP
        while kill -0 "$MAIN_PID" 2>/dev/null; do
            sleep "$SUDO_KEEPALIVE_INTERVAL" &
            wait "$!" 2>/dev/null || true
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

run_logged_command() {
    local -a cmd=("$@")
    local rc=0 timestamp arg
    if [[ -z "$LOG_FILE" || ! -w "$LOG_FILE" ]]; then
        ( [[ -n "${LOCK_FD:-}" ]] && exec {LOCK_FD}>&- 2>/dev/null || true; "${cmd[@]}" ) || rc=$?
        return "$rc"
    fi
    printf -v timestamp '%(%H:%M:%S)T' -1
    { printf '[%s] [SCRIPT ] BEGIN' "$timestamp"; for arg in "${cmd[@]}"; do printf ' %q' "$arg"; done; printf '\n'; } >> "$LOG_FILE"
    ( [[ -n "${LOCK_FD:-}" ]] && exec {LOCK_FD}>&- 2>/dev/null || true; "${cmd[@]}" >> "$LOG_FILE" 2>&1 ) || rc=$?
    printf -v timestamp '%(%H:%M:%S)T' -1
    printf '[%s] [SCRIPT ] END rc=%d\n' "$timestamp" "$rc" >> "$LOG_FILE"
    return "$rc"
}

resolve_manifest_paths() {
    local script script_path is_custom i failures=0 script_dir_missing=false
    [[ -d "$SCRIPT_DIR" ]] || { script_dir_missing=true; log WARN "Default script directory is missing: $SCRIPT_DIR"; }
    for i in "${!MANIFEST_MODE[@]}"; do
        script="${MANIFEST_SCRIPT[$i]}"
        if [[ -v "CUSTOM_SCRIPT_PATHS[$script]" && -n "${CUSTOM_SCRIPT_PATHS[$script]}" ]]; then
            script_path="${WORK_TREE}/${CUSTOM_SCRIPT_PATHS[$script]}"; is_custom=true
        else
            script_path="${SCRIPT_DIR}/${script}"; is_custom=false
        fi
        MANIFEST_PATH[$i]="$script_path"; MANIFEST_IS_CUSTOM[$i]="$is_custom"
        if [[ "$is_custom" != true && "$script_dir_missing" == true ]]; then MANIFEST_PATH_STATE[$i]=missing
        elif [[ -e "$script_path" || -L "$script_path" ]]; then
            if [[ ! -f "$script_path" ]]; then MANIFEST_PATH_STATE[$i]=not-a-file
            elif [[ ! -r "$script_path" ]]; then MANIFEST_PATH_STATE[$i]=unreadable
            else MANIFEST_PATH_STATE[$i]=ok
            fi
        else MANIFEST_PATH_STATE[$i]=missing
        fi
        case "${MANIFEST_PATH_STATE[$i]}" in
            ok) ;;
            missing) log ERROR "Required script not found: $script -> $(quote_for_log "$script_path")"; HARD_FAILED_SCRIPTS+=("$script (missing)"); (( failures++ )) || true ;;
            unreadable) log ERROR "Required script unreadable: $script -> $(quote_for_log "$script_path")"; HARD_FAILED_SCRIPTS+=("$script (unreadable)"); (( failures++ )) || true ;;
            not-a-file) log ERROR "Required script path is not a regular file: $script -> $(quote_for_log "$script_path")"; HARD_FAILED_SCRIPTS+=("$script (not-a-file)"); (( failures++ )) || true ;;
        esac
    done
    (( failures == 0 )) || { log ERROR "Aborting execution due to ${failures} manifest path error(s)."; return 1; }
}

execute_scripts() {
    log SECTION 'Executing Update Sequence'
    resolve_manifest_paths || return 1
    local i total="${#MANIFEST_MODE[@]}" mode script ignore_fail script_path quoted_args rc choice
    local -a args=()
    for i in "${!MANIFEST_MODE[@]}"; do
        mode="${MANIFEST_MODE[$i]}"; script="${MANIFEST_SCRIPT[$i]}"; ignore_fail="${MANIFEST_IGNORE_FAIL[$i]}"; script_path="${MANIFEST_PATH[$i]}"
        local -n argv_ref="${MANIFEST_ARGV_NAME[$i]}"
        args=("${argv_ref[@]}")
        join_quoted_argv "${args[@]}"; quoted_args="$REPLY"
        printf '%s[%d/%d]%s %s->%s %s%s%s\n' "$CLR_CYN" "$((i + 1))" "$total" "$CLR_RST" "$CLR_BLU" "$CLR_RST" "$script" "${quoted_args:+ }" "$quoted_args"
        if [[ "$OPT_DRY_RUN" == true ]]; then continue; fi
        if [[ "$mode" == S && -z "$SUDO_PID" ]]; then
            init_sudo || { HARD_FAILED_SCRIPTS+=("$script (sudo auth)"); return 1; }
        fi
        rc=0
        case "$mode" in
            S) run_logged_command sudo "$BASH_BIN" "$script_path" "${args[@]}" || rc=$? ;;
            U) run_logged_command "$BASH_BIN" "$script_path" "${args[@]}" || rc=$? ;;
        esac
        (( rc == 0 )) && continue
        if [[ "$ignore_fail" == true ]]; then
            log WARN "$script failed (exit $rc), ignored via ignore-fail."
            SOFT_FAILED_SCRIPTS+=("$script")
            continue
        fi
        log ERROR "$script failed (exit $rc)."
        if [[ -t 0 && "$OPT_FORCE" != true ]]; then
            while true; do
                read -r -p 'Choose [R]etry, [S]kip, or [Q]uit: ' choice || choice=q
                case "${choice,,}" in
                    r|retry)
                        log INFO "Retrying $script..."
                        rc=0
                        case "$mode" in
                            S) run_logged_command sudo "$BASH_BIN" "$script_path" "${args[@]}" || rc=$? ;;
                            U) run_logged_command "$BASH_BIN" "$script_path" "${args[@]}" || rc=$? ;;
                        esac
                        (( rc == 0 )) && break
                        log ERROR "$script failed again (exit $rc)."
                        ;;
                    s|skip)
                        log WARN "Skipping required script: $script"
                        SKIPPED_SCRIPTS+=("$script")
                        HARD_FAILED_SCRIPTS+=("$script (skipped)")
                        break
                        ;;
                    *)
                        HARD_FAILED_SCRIPTS+=("$script")
                        return 1
                        ;;
                esac
            done
            [[ "${choice,,}" == s || "${choice,,}" == skip ]] && continue
            (( rc == 0 )) && continue
        else
            HARD_FAILED_SCRIPTS+=("$script")
            return 1
        fi
    done
}

# ==============================================================================
# SUMMARY / CLEANUP
# ==============================================================================
print_summary() {
    [[ "$SKIP_FINAL_SUMMARY" == true || "$SUMMARY_PRINTED" == true ]] && return 0
    SUMMARY_PRINTED=true
    printf '\n'; log SECTION 'Summary'
    [[ "$OPT_DRY_RUN" == true ]] && log INFO 'Dry run complete - no changes were made.'
    [[ "$SYNC_FAILED" == true ]] && log WARN 'Sync phase did not complete successfully.'
    local fs
    if ((${#HARD_FAILED_SCRIPTS[@]})); then
        log ERROR "${#HARD_FAILED_SCRIPTS[@]} required script(s) failed or were skipped:"
        for fs in "${HARD_FAILED_SCRIPTS[@]}"; do log RAW "    * $fs"; done
    elif [[ "$SYNC_FAILED" != true && ( "$CURRENT_PHASE" == 'script execution' || "$CURRENT_PHASE" == summary || "$CURRENT_PHASE" == cleanup ) ]]; then
        log OK 'All required operations completed successfully.'
    fi
    if ((${#SOFT_FAILED_SCRIPTS[@]})); then
        log WARN "${#SOFT_FAILED_SCRIPTS[@]} script(s) soft-failed:"
        for fs in "${SOFT_FAILED_SCRIPTS[@]}"; do log RAW "    * $fs"; done
    fi
    if ((${#SKIPPED_SCRIPTS[@]})); then
        log INFO "${#SKIPPED_SCRIPTS[@]} script(s) skipped:"
        for fs in "${SKIPPED_SCRIPTS[@]}"; do log RAW "    * $fs"; done
    fi
    [[ -n "$LOG_FILE" ]] && log INFO "Log saved to: $LOG_FILE"
}

cleanup() {
    local rc=$? tdir cdir
    CURRENT_PHASE=cleanup
    stop_sudo
    release_lock
    for tdir in "${CREATED_TEMP_DIRS[@]}"; do
        [[ -n "$tdir" && -d "$tdir" ]] && rm -rf -- "$tdir" 2>/dev/null || true
    done
    if [[ -n "$USER_MODS_BACKUP_DIR" && -d "$USER_MODS_BACKUP_DIR" ]]; then
        printf '\n'; log WARN 'Update incomplete. Modified files are preserved at:'; printf '    %s\n' "$USER_MODS_BACKUP_DIR"
    fi
    if ((${#COLLISION_BACKUP_DIRS[@]})); then
        printf '\n'; log INFO 'Work-tree collision backups preserved at:'
        for cdir in "${COLLISION_BACKUP_DIRS[@]}"; do [[ -d "$cdir" ]] && printf '    %s\n' "$cdir"; done
    fi
    [[ -n "$FULL_TRACKED_BACKUP_DIR" && -d "$FULL_TRACKED_BACKUP_DIR" ]] && { log INFO 'Full tracked tree backup preserved at:'; printf '    %s\n' "$FULL_TRACKED_BACKUP_DIR"; }
    [[ -n "$GIT_HISTORY_BACKUP_DIR" && -d "$GIT_HISTORY_BACKUP_DIR" ]] && { log INFO 'Git history backup preserved at:'; printf '    %s\n' "$GIT_HISTORY_BACKUP_DIR"; }
    print_summary
    if ((${#HARD_FAILED_SCRIPTS[@]})); then desktop_notify critical 'Dusky Update' "${#HARD_FAILED_SCRIPTS[@]} required script(s) failed"; exit 1
    elif (( rc != 0 )); then desktop_notify critical 'Dusky Update' 'Update failed or interrupted'; exit "$rc"
    elif [[ "$SYNC_FAILED" == true ]]; then desktop_notify critical 'Dusky Update' 'Sync phase failed'; exit 1
    else desktop_notify normal 'Dusky Update' 'Update completed successfully'; exit 0
    fi
}

# ==============================================================================
# MAIN
# ==============================================================================
main() {
    CURRENT_PHASE=startup
    parse_args "${ORIGINAL_ARGS[@]}"
    ensure_not_running_as_root
    check_dependencies

    if [[ -t 0 && "$OPT_FORCE" != true && "$OPT_POST_SELF_UPDATE" != true ]]; then
        printf '\n%sNote:%s Avoid interrupting the update during Git operations.\n\n' "$CLR_YLW" "$CLR_RST"
        local start_confirm=""
        read -r -p 'Start the update? [y/N] ' start_confirm
        [[ "$start_confirm" =~ ^[Yy]$ ]] || { printf 'Update cancelled.\n'; exit 0; }
    fi

    trap cleanup EXIT
    trap 'log WARN "Interrupted by user (SIGINT)"; exit 130' INT
    trap 'log WARN "Terminated (SIGTERM)"; exit 143' TERM
    trap 'log WARN "Hangup signal received (SIGHUP)"; exit 129' HUP

    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO 'Running in DRY-RUN mode - no changes will be made.'
    else
        setup_storage_roots
        setup_runtime_dir
        setup_logging
        auto_prune
        acquire_lock || exit 1
    fi

    CURRENT_PHASE=preflight
    parse_update_sequence_manifest
    require_sudo_if_needed || exit 1

    local self_hash_before="" self_hash_after="" cont=n sync_rc=0
    if [[ "$OPT_DRY_RUN" != true && "$OPT_POST_SELF_UPDATE" != true && -r "$SELF_PATH" ]]; then
        file_sha256 "$SELF_PATH" && self_hash_before="$REPLY" || true
    fi

    CURRENT_PHASE=sync
    if [[ "$OPT_SKIP_SYNC" != true && "$OPT_POST_SELF_UPDATE" != true ]]; then
        if pull_updates; then
            if [[ "$OPT_DRY_RUN" != true && -n "$self_hash_before" && -r "$SELF_PATH" ]]; then
                file_sha256 "$SELF_PATH" && self_hash_after="$REPLY" || true
                if [[ -n "$self_hash_after" && "$self_hash_before" != "$self_hash_after" ]]; then
                    log SECTION 'Self-Update Detected'
                    log OK 'Reloading updated script...'
                    CURRENT_PHASE=self-reexec
                    SKIP_FINAL_SUMMARY=true
                    stop_sudo; release_lock
                    local -a reexec_args=(--post-self-update)
                    [[ "$OPT_DRY_RUN" == true ]] && reexec_args+=(--dry-run)
                    [[ "$OPT_FORCE" == true ]] && reexec_args+=(--force)
                    [[ "$OPT_SKIP_SYNC" == true ]] && reexec_args+=(--skip-sync)
                    [[ "$OPT_SYNC_ONLY" == true ]] && reexec_args+=(--sync-only)
                    [[ "$OPT_STOP_ON_FAIL" == true ]] && reexec_args+=(--stop-on-fail)
                    [[ "$OPT_ALLOW_DIVERGED_RESET" == true ]] && reexec_args+=(--allow-diverged-reset)
                    exec "$BASH_BIN" "$SELF_PATH" "${reexec_args[@]}"
                fi
            fi
        else
            sync_rc=$?; SYNC_FAILED=true; log WARN 'Sync failed.'
            [[ "$OPT_SYNC_ONLY" == true ]] && exit 1
            if (( sync_rc == SYNC_RC_RECOVERABLE )) && [[ -t 0 ]]; then
                read -r -t "$PROMPT_TIMEOUT_SHORT" -p 'Continue with local scripts? [y/N] ' cont || cont=n
            fi
            [[ "$cont" =~ ^[Yy]$ ]] || exit 1
        fi
    fi

    if [[ "$OPT_SYNC_ONLY" == true ]]; then
        log OK 'Sync-only mode - skipping script execution.'
    elif [[ "$SYNC_FAILED" != true || "$cont" =~ ^[Yy]$ ]]; then
        CURRENT_PHASE='script execution'
        execute_scripts
    fi
    CURRENT_PHASE=summary
}

main
