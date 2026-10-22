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
#    1. SCRIPT_SEARCH_DIRS  (Tells the engine WHERE to look for scripts)
#    2. UPDATE_SEQUENCE     (Tells the engine WHEN and HOW to run a script)
#
# ==============================================================================
#  1. THE SCRIPT_SEARCH_DIRS ARRAY (The "Where")
# ==============================================================================
#  Directories to search for scripts (in order).
#  By default, the engine will scan these paths relative to your home/work tree.
#  Entries WITHOUT a '/' in the name are searched across these directories.
#  Entries WITH a '/' are treated as direct paths.
#  If a name exists in multiple directories the engine prompts to choose
#  unless SCRIPT_CONFLICT_RESOLUTIONS pre-selects a path.
#
#  ⚠️ CRITICAL RULE: Adding a directory here DOES NOT run its scripts! It only acts 
#  as a search path for the engine. To actually run a script, you must ALSO add it 
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
#  By default, if a required script fails the updater stops (in an
#  interactive terminal you may explicitly choose skip/retry unless
#  --stop-on-fail forces immediate abort).
#    'ignore-fail' = If this specific script fails,
#                    log a warning but CONTINUE updating.
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
#     "S | 060_package_installation.py"
#
#  3. Sudo script passing arguments (--auto and --force):
#     "S | 050_pacman_config.sh --auto --force"
#
#  4. User script that is ALLOWED TO FAIL (won't stop the updater):
#     "U | ignore-fail | 146_wallpapers_download.sh --quiet"
#
#  5. Sudo script allowed to fail:
#     "S | ignore-fail | 085_warp.py --connect"
# ==============================================================================
set -euo pipefail
shopt -s extglob

export PYTHONUNBUFFERED=1 # Unbuffer Python outputs explicitly ensuring real-time log piping.

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
declare -ri DISK_MIN_FREE_MB=100
declare -ri DISK_COPY_RESERVE_MB=64
declare -r VERSION="8.0.3"
declare -ri SYNC_RC_RECOVERABLE=10
declare -ri SYNC_RC_UNSAFE=20

# ==============================================================================
# CONFIGURATION
# ==============================================================================
declare -r DOTFILES_GIT_DIR="${HOME}/dusky"
declare -r WORK_TREE="${HOME}"
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
# SCRIPT SEARCH DIRECTORIES Guide
# ------------------------------------------------------------------------------
# Directories to search for scripts (in order).
# If a script in UPDATE_SEQUENCE does not contain a '/', it is searched here.
# A name found in multiple directories prompts for a choice unless
# SCRIPT_CONFLICT_RESOLUTIONS pre-selects a path.
#
# Format: "${WORK_TREE}/path/from/home/directory"
#
# Example:
#   "${WORK_TREE}/user_scripts/networking"
#   Then in UPDATE_SEQUENCE add:
#     "S | warp_toggle.py"

declare -a SCRIPT_SEARCH_DIRS=(
    "${WORK_TREE}/user_scripts/arch_setup_scripts/scripts"
    "${WORK_TREE}/user_scripts/arch_setup_scripts"
    "${WORK_TREE}/user_scripts/networking"
    "${WORK_TREE}/user_scripts/misc_extra"
    "${WORK_TREE}/user_scripts/misc_extra/delete_in_3_weeks"
    "${WORK_TREE}/user_scripts/update_dusky/update_checker"
    "${WORK_TREE}/user_scripts/dusky_system/reload_cc"
    "${WORK_TREE}/user_scripts/services"
    "${WORK_TREE}/user_scripts/update_dusky"
    "${WORK_TREE}/user_scripts/rofi"
    "${WORK_TREE}/user_scripts/images"
    "${WORK_TREE}/user_scripts/theme_matugen/config"
    "${WORK_TREE}/user_scripts/firefox/theme_matugen"
    "${WORK_TREE}/user_scripts/firefox"
    "${WORK_TREE}/user_scripts/theme_matugen"
    "${WORK_TREE}/user_scripts/waybar"
    "${WORK_TREE}/user_scripts/starship"
    "${WORK_TREE}/user_scripts/tts_stt/dusky_kokoro"
    "${WORK_TREE}/user_scripts/tts_stt/dusky_parakeet"
)

# ------------------------------------------------------------------------------
# SCRIPT CONFLICT RESOLUTIONS
# ------------------------------------------------------------------------------
# If a script exists in multiple search directories, the engine will normally 
# prompt you at startup to choose which one to run. You can pre-configure the 
# exact path here to bypass the prompt and run autonomously.
#
# Format: ["script_name.sh"]="path/relative/to/home/script_name.sh"
#
# TIP: If you want to run BOTH versions of the script at different times in 
# your sequence, do NOT use this array. Instead, provide the full relative 
# path directly in the UPDATE_SEQUENCE (e.g. "U | folderA/script.sh" and 
# "U | folderB/script.sh"). The engine natively handles this perfectly.
declare -A SCRIPT_CONFLICT_RESOLUTIONS=(
    # ["update_checker.sh"]="user_scripts/update_dusky/update_checker.sh"
)

# ------------------------------------------------------------------------------
# UPDATE SEQUENCE
# ------------------------------------------------------------------------------
declare -ra UPDATE_SEQUENCE=(

#================= CUSTOM=====================
    "U | backup_hyprlang_files.sh"
    "U | 480_dusky_commands.py -b"
#================= Scripts =====================

#    "U | 002_pre_generated_colors.sh"
#    "U | 003_network_connect.sh"
    "U | 005_hypr_custom_config_setup.py"
    "U | 006_animation_default.sh"
#    "U | 005_hypr_custom_config_setup.py --force --workspace_rules"
    "U | 005_hypr_custom_config_setup.py --force --environment_variables"
    "U | 005_hypr_custom_config_setup.py --force --autostart"
    "U | 010_package_removal.sh --auto"


#================= CUSTOM=====================
    "S | pacman_packages.sh"
    "U | paru_packages.sh"
#================= Scripts =====================

    "U | 015_set_thunar_terminal.py -t foot"
    "U | 020_desktop_entries.py"
    "U | 025_configure_keyboard.sh"
#    "U | 040_long_sleep_timeout.sh"
#    "S | 045_battery_limiter.sh"
#    "S | 050_pacman_config.sh --auto"
    "S | 051_pacman_hooks.sh --auto"
#    "S | 055_pacman_reflector.sh"
#    "S | 058_aur_paru_fallback_yay.sh"
#    "S | 060_package_installation.py"
#    "S | 070_openssh_setup.sh"
#    "U | 075_changing_shell_zsh.sh"
#    "S | 085_warp.py"
#    "U | 090_paru_packages_optional.sh"
#    "S | 095_battery_limiter_again_dusk.sh"
#    "U | 100_paru_packages.sh"
#    "S | 120_create_mount_directories.sh"
    "S | 127_pam_keyring_greetd.py --mode auto"
    "U | 130_systemd_dbus_service_manager.py --default"
#    "U | 135_battery_notify_service.sh"
#    "U | 137_snapper_isolation_subvolume.sh --auto"
#    "U | 140_dusky_font_configurator.py"
    "U | 145_matugen_directories.py"
#    "U | 146_wallpapers_download.sh"
#    "U | 155_blur_shadow_opacity.sh"
#    "U | ignore-fail | 160_theme_ctl.py"
#    "U | 165_qtct_config.sh"
    "S | 180_udev_usb_notify.sh"
#    "S | 190_dusk_fstab.py"
#    "S | firefox_symlink_partition.py"
#    "S | 200_tlp_config.py"
#    "S | 205_zram_configuration.sh"
#    "S | 210_zram_optimize_swappiness.sh"
    "S | 211_systemd_oomd_zram.py"
#    "S | 215_powerkey_lid_close_behaviour.sh"
#    "S | 220_logrotate_optimization.sh"
    "S | 225_faillock_timeout.py --preset lenient -y"
#    "U | 230_asus_tuf_tweaks.sh"
    "U | 235_default_apps.py --apply-state"
#    "S | 245_asusd_service_fix.sh"
#    "S | 250_ftp_arch.sh"
#    "U | 255_tldr_update.sh"
#    "U | 260_spotify.sh"
#    "U | 265_mouse_button_reverse.sh --right"
    "U | 290_dusky_service_toggler.py --default"
#    "S | 295_initramfs_optimization.py"
#    "U | 300_git_config.sh"
#    "U | user_scripts/git/dusky_backup_manager.py --new"
#    "U | user_scripts/git/dusky_backup_manager.py --relink"
#    "S | 320_systemdboot_optimization.py --auto"
#    "S | 325_hosts_files_block.sh"
#    "S | 330_gtk_root_symlink.sh"
#    "S | 335_preload_config.sh"
#    "S | 350_dns_systemd_resolve.sh"
#    "U | 360_obsidian_pensive_vault_configure.sh"
#    "U | 365_cache_purge.sh"
#    "S | 370_arch_install_scripts_cleanup.sh"
#    "U | 375_cursor_theme_bibata_classic_modern.sh"
#    "S | 380_nvidia_open_source.sh"
    "U | 383_configure_hyprland_gpu.py --auto"
#    "S | 385_waydroid_setup.sh"
    "U | 390_clipboard_persistance.py --ram --quiet"
#    "S | 395_intel_media_sdk_check.sh"
#    "U | 405_spicetify_matugen_setup.sh"
#    "U | 410_waybar_swap_config.py --state"
#    "U | 415_mpv_setup.sh"
#    "S | 430_btrfs_zstd_compression_stats.sh"
    "U | 434_wayclick_soundpacks_download.sh --auto"
#    "U | 435_key_sound_wayclick_setup.sh --setup"
#    "U | 440_config_bat_notify.sh --default"
    "U | 455_hyprctl_reload.sh"
#    "U | 460_switch_clipboard.sh --terminal --force" no longer required!
#    "S | 465_sddm_setup.sh"
#    "U | 470_vesktop_matugen.sh"
    "S | 473_add_user_to_group.sh --auto"
#    "U | 475_reverting_sleep_timeout.sh"
#    "U | 480_dusky_commands.py"
    "S | 485_sudoers_nopassword.sh"

#================= CUSTOM=====================

    "U | copy_service_files.sh --default"
    "U | update_checker.sh --num"
#    "U | cc_restart.sh --quiet"
    "U | ignore-fail | wallpaper_selector.py --build-cache"
#    "U | append_defaults_keybinds_edit_here.sh"
    "U | ignore-fail | tui_matugen.py --smart"
    "U | ignore-fail | hypr_anim.sh --current"
    "U | ignore-fail | theme_ctl.sh refresh"
    "U | dusky_sites_setup.py --update-installed --yes"
    "U | ignore-fail | update_counter.sh"
    "U | tui_starship.py --apply-state"
    "U | 480_dusky_commands.py -a"
#    "U | system_update.sh --pacman"
#
#
    "S | fix_wayland_session.py"
)

# ==============================================================================
# BINARIES / STATIC RUNTIME
# ==============================================================================
declare -g GIT_BIN=""
declare -g BASH_BIN=""
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
declare -g RUN_TIMESTAMP=""
RUN_TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
readonly RUN_TIMESTAMP
declare -g SELF_PATH=""
SELF_PATH="$(realpath -- "$0")"
readonly SELF_PATH
declare -g CACHED_USER=""
CACHED_USER="${USER:-$(id -un 2>/dev/null || printf '%s' unknown)}"
readonly CACHED_USER

declare -ga ORIGINAL_ARGS=("$@")
declare -ga GIT_CMD=("$GIT_BIN" --git-dir="$DOTFILES_GIT_DIR" --work-tree="$WORK_TREE")

# ==============================================================================
# MUTABLE RUNTIME STATE
# ==============================================================================
declare -g ACTIVE_LOG_BASE_DIR=""
declare -g ACTIVE_BACKUP_BASE_DIR=""
declare -g LOG_FILE=""
declare -g SUDO_PID=""
declare -g SUDO_INITIALIZED=false
declare -ga ACTIVE_LOGGER_PIDS=()
declare -g CURRENT_PHASE="startup"
declare -g FAILED_PHASE=""
declare -g SUMMARY_PRINTED=false
declare -g SKIP_FINAL_SUMMARY=false
declare -g SYNC_FAILED=false
declare -g EXECUTE_RC=0
declare -g INTERRUPTED=false
declare -g FAILED_COMMAND=""
declare -g FAILED_EXIT=0
declare -g HANDOFF_FILE=""

declare -g USER_MODS_BACKUP_DIR=""
declare -g USER_MODS_BACKUP_COMPLETE=false
declare -g FULL_TRACKED_BACKUP_DIR=""
declare -g FULL_TRACKED_BACKUP_COMPLETE=false
declare -g GIT_HISTORY_BACKUP_DIR=""
declare -g GIT_HISTORY_BACKUP_COMPLETE=false
declare -g MERGE_DIR=""
declare -g PRE_SYNC_HEAD=""
declare -g PRE_SYNC_HEAD_OID=""
declare -g PINNED_REMOTE_OID=""

declare -ga CREATED_TEMP_DIRS=()
declare -gA CREATED_TEMP_STATUS=()
declare -gA TEMP_DISPLACED_FOR_TMPDIR=()
declare -ga COLLISION_BACKUP_DIRS=()
declare -gA COLLISION_MOVED_PATHS=()
declare -ga HARD_FAILED_SCRIPTS=()
declare -ga SOFT_FAILED_SCRIPTS=()
declare -ga SKIPPED_SCRIPTS=()
declare -ga EXECUTED_SCRIPTS=()
declare -gA FAILED_SCRIPT_DIRS=()
declare -gA SCRIPT_RESOLUTION_CACHE=()
declare -gA INTERPRETER_CACHE=()

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
declare -ga MANIFEST_INTERPRETER=()
declare -ga MANIFEST_INTERPRETER_ARGV_NAME=()

declare -g OPT_DRY_RUN=false
declare -g OPT_SKIP_SYNC=false
declare -g OPT_SYNC_ONLY=false
declare -g OPT_FORCE=false
declare -g OPT_STOP_ON_FAIL=false
declare -g OPT_POST_SELF_UPDATE=false
declare -g OPT_ALLOW_DIVERGED_RESET=false

# ==============================================================================
# COLORS
# ==============================================================================
if [[ -n "${NO_COLOR:-}" ]]; then
    declare -r CLR_RED=""
    declare -r CLR_GRN=""
    declare -r CLR_YLW=""
    declare -r CLR_BLU=""
    declare -r CLR_CYN=""
    declare -r CLR_RST=""
elif [[ -t 1 ]]; then
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

parse_decimal_choice() {
    local input="${1-}"
    local min="${2:-1}"
    local max="${3:-999999}"
    local -n out_ref="$4"
    local dec=0

    [[ "$input" =~ ^[0-9]+$ ]] || return 1
    ((${#input} <= 9)) || return 1
    # Force base-10 to avoid octal interpretation of leading zeros.
    dec=$((10#$input)) || return 1
    ((dec >= min && dec <= max)) || return 1
    out_ref="$dec"
    return 0
}

normalize_explicit_script_path() {
    local script="${1-}"
    REPLY=""

    if [[ "${script:0:2}" == "~/" ]]; then
        REPLY="${WORK_TREE}/${script#~/}"
        return 0
    fi
    if [[ "${script:0:1}" == "~" ]]; then
        return 1
    fi
    if [[ "$script" == /* ]]; then
        REPLY="$script"
        return 0
    fi
    REPLY="${WORK_TREE}/${script}"
    return 0
}

is_safe_worktree_relative() {
    local rel="${1-}"
    [[ -n "$rel" ]] || return 1
    [[ "$rel" != /* ]] || return 1
    [[ "$rel" != *$'\n'* ]] || return 1
    case "$rel" in
        ..|../*|*/../*|*/..) return 1 ;;
    esac
    return 0
}

is_hex_oid() {
    local oid="${1-}"
    [[ "$oid" =~ ^[0-9a-f]{40}$ || "$oid" =~ ^[0-9a-f]{64}$ ]] || return 1
    return 0
}

is_zero_oid() {
    local oid="${1-}"
    [[ "$oid" =~ ^0{40}$ || "$oid" =~ ^0{64}$ ]] && return 0
    return 1
}

has_symlink_ancestor() {
    local abs_path="${1-}"
    local cur=""
    local parent=""

    # Start at the PARENT: ordinary leaf symlinks (regular or dangling) are
    # allowed — cp -a/mv preserve the link. Only actual parent traversal
    # that would move/copy outside the worktree is rejected.
    cur="$(path_parent "$abs_path")"

    while true; do
        if [[ -L "$cur" ]]; then
            return 0
        fi
        if [[ "$cur" == "$WORK_TREE" || "$cur" == "/" || "$cur" == "." ]]; then
            return 1
        fi
        parent="$(path_parent "$cur")"
        [[ "$parent" != "$cur" ]] || return 1
        cur="$parent"
        # Component-safe work-tree prefix check (avoid /home/user matching /home/user2).
        if [[ "$cur" != "$WORK_TREE" && "$cur" != "$WORK_TREE"/* ]]; then
            return 1
        fi
    done
}

validate_restore_parent_topology() {
    local target="${1-}"
    local parent="" cur="" comp="" rel=""
    local -a parts=()

    [[ "$target" == "$WORK_TREE"/* || "$target" == "$WORK_TREE" ]] || return 1
    parent="$(path_parent "$target")"
    # Walk each prefix of the parent relative to WORK_TREE and reject symlinks.
    rel="${parent#"$WORK_TREE"}"
    rel="${rel#/}"
    [[ -z "$rel" ]] && return 0
    IFS='/' read -r -a parts <<< "$rel" || return 1
    cur="$WORK_TREE"
    for comp in "${parts[@]}"; do
        [[ -n "$comp" && "$comp" != "." && "$comp" != ".." ]] || return 1
        cur="${cur}/${comp}"
        if [[ -L "$cur" ]]; then
            return 1
        fi
        if [[ -e "$cur" && ! -d "$cur" ]]; then
            return 1
        fi
    done
    return 0
}

# Structurally parse a shebang line into an argv array without eval.
# Usage: parse_shebang_argv <script_path> <out_array_name>
# Returns 0 with array set when a supported shebang exists,
# 2 when no shebang is present, 1 when unsupported/malformed.
parse_shebang_argv() {
    local script_path="${1-}"
    local out_name="$2"
    local -n out_ref="$out_name"
    local first_line=""
    local rest="" prog="" base=""
    local -a words=()
    local -a result=()
    local i=0

    out_ref=()
    if [[ ! -f "$script_path" || ! -r "$script_path" ]]; then
        return 1
    fi
    if ! IFS= read -r first_line < "$script_path" 2>/dev/null; then
        # Empty/unreadable first line with readable file means no shebang.
        # Distinguish unreadable (above) from empty (no shebang).
        first_line=""
    fi
    first_line="${first_line%$'\r'}"
    [[ "$first_line" == '#!'* ]] || return 2
    rest="${first_line#\#!}"
    rest="$(trim "$rest")"
    [[ -n "$rest" ]] || return 1
    case "$rest" in
        *\'*|*\"*|*\\*|*$'\n'*) return 1 ;;
    esac
    read -r -a words <<< "$rest" || return 1
    ((${#words[@]} > 0)) || return 1

    prog="${words[0]}"
    base="$(path_base "$prog")"
    if [[ "$base" == "env" ]]; then
        ((${#words[@]} >= 2)) || return 1
        i=1
        # Optional single "-S" (env --split-string) with already-split args.
        if [[ "${words[$i]}" == "-S" ]]; then
            ((i++))
            ((${#words[@]} > i)) || return 1
        fi
        # Strict: after optional -S, next word MUST be the interpreter.
        # Never silently strip env assignments/options (e.g. FOO=bar, -i/-u/-C).
        # Either preserve supported semantics or reject; we reject unsupported
        # env forms explicitly to avoid silent reinterpretation.
        # Supported: env [-S] interpreter [interpreter-args...]
        # Unsupported (rejected): env assignments (FOO=bar), env options (-i/-u/-C/-v/...).
        ((i < ${#words[@]})) || return 1
        case "${words[$i]}" in
            -*|*=*|*\'*|*\"*) return 1 ;;
        esac
        # Disallow nested env.
        [[ "$(path_base "${words[$i]}")" != "env" ]] || return 1
        result=("${words[@]:$i}")
        [[ -n "${result[0]}" ]] || return 1
        out_ref=("$prog" "${result[@]}")
        return 0
    fi

    # Direct interpreter path or bare name.
    result=("${words[@]}")
    out_ref=("${result[@]}")
    return 0
}

resolve_interpreter_for_script() {
    local script_path="${1-}"
    local out_display_name="$2"
    local out_argv_name="$3"
    local -n display_ref="$out_display_name"
    local -n argv_out_ref="$out_argv_name"
    local first_line=""
    local -a shebang_argv=()
    local shebang_rc=0
    local has_py_ext=false has_sh_ext=false
    local interp_base="" first_prog="" first_base=""
    local -a candidate=()
    local needs_py=false is_bash_like=false is_python_like=false

    display_ref=""
    argv_out_ref=()
    [[ "$script_path" == *.py ]] && has_py_ext=true
    [[ "$script_path" == *.sh ]] && has_sh_ext=true

    parse_shebang_argv "$script_path" shebang_argv
    shebang_rc=$?
    if ((shebang_rc == 1)); then
        return 1
    fi

    if ((shebang_rc == 0)); then
        first_prog="${shebang_argv[0]}"
        first_base="$(path_base "$first_prog")"
        if [[ "$first_base" == "env" ]]; then
            # shebang_argv is (env interpreter args...); interpreter is element 1.
            candidate=("${shebang_argv[@]:1}")
            interp_base="$(path_base "${candidate[0]}")"
        else
            candidate=("${shebang_argv[@]}")
            interp_base="$(path_base "${candidate[0]}")"
        fi
        case "$interp_base" in
            python|python3|python3.*)
                is_python_like=true
                ;;
            bash|sh|dash|ksh|zsh)
                # Direct path like /bin/bash or bare "bash" via env.
                is_bash_like=true
                ;;
            *)
                # Unsupported interpreter (e.g. perl, node, ruby).
                return 1
                ;;
        esac
        # Extension contradiction: .py must be python-like, .sh must be bash-like.
        if [[ "$has_py_ext" == true && "$is_bash_like" == true ]]; then
            return 2
        fi
        if [[ "$has_sh_ext" == true && "$is_python_like" == true ]]; then
            return 2
        fi
        if [[ "$is_python_like" == true ]]; then
            needs_py=true
        fi
        # Verify the actual interpreter binary is available.
        if [[ "$first_base" == "env" ]]; then
            # candidate[0] may be bare name like python3/bash; resolve via PATH.
            if ! command -v "${candidate[0]}" >/dev/null 2>&1; then
                # Allow absolute candidate that is executable even if not in PATH.
                if [[ "${candidate[0]}" == /* ]]; then
                    [[ -x "${candidate[0]}" ]] || return 1
                else
                    return 1
                fi
            fi
            # Store full env argv for execution to preserve -S args.
            argv_out_ref=("${shebang_argv[@]}")
            display_ref="$(join_quoted_argv "${shebang_argv[@]}")"
        else
            # Direct path: must be executable; bare name must be in PATH.
            if [[ "${candidate[0]}" == /* ]]; then
                [[ -x "${candidate[0]}" ]] || return 1
            else
                command -v "${candidate[0]}" >/dev/null 2>&1 || return 1
            fi
            argv_out_ref=("${candidate[@]}")
            display_ref="$(join_quoted_argv "${candidate[@]}")"
        fi
        if [[ "$needs_py" == true ]]; then
            printf -v REPLY '%s' "python"
        fi
        return 0
    fi

    # No shebang: decide by extension.
    if [[ "$has_py_ext" == true ]]; then
        if command -v python3 >/dev/null 2>&1; then
            argv_out_ref=("python3")
            display_ref="python3"
            return 0
        elif command -v python >/dev/null 2>&1; then
            argv_out_ref=("python")
            display_ref="python"
            return 0
        else
            return 1
        fi
    elif [[ "$has_sh_ext" == true ]]; then
        argv_out_ref=("$BASH_BIN")
        display_ref="$BASH_BIN"
        return 0
    else
        # No extension and no shebang: default to bash for safety, but require
        # executable bit? Still use bash.
        argv_out_ref=("$BASH_BIN")
        display_ref="$BASH_BIN"
        return 0
    fi
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

Usage: update_dusky.sh [OPTIONS]

Options:
  --help, -h               Show this help message and exit
  --version                Show version and exit
  --dry-run                Preview actions without making changes (non-interactive, read-only)
  --skip-sync              Skip git sync, only run the script sequence
  --sync-only              Pull updates but do not run scripts
  --force                  Skip confirmation prompts (never authorizes diverged/unrelated reset)
  --stop-on-fail           Abort immediately on first required failure (no prompt, no auto-retry)
  --allow-diverged-reset   In non-interactive/force mode, allow reset on diverged or unrelated history
  --list                   List all active scripts in the update sequence

Update sequence entry formats:
  U | script.sh --auto
  S | ignore-fail | script.sh --auto
  U | | script.sh --auto

Field 1:
  U = run as user
  S = run with sudo

Field 2:
  Optional flags. Supported value:
    ignore-fail

Rules:
  - Arguments are whitespace-separated only
  - Quotes, backslash escapes, newlines, and extra "|" characters in the command field are not supported
  - Failure default: required failures stop the updater; interactive TTY may
    explicitly choose skip/retry unless --stop-on-fail is set

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
# MANIFEST PARSING & RESOLUTION
# ==============================================================================
validate_search_dirs() {
    local needs_search_dirs=0
    local i=""
    local valid=0
    local dir=""

    for i in "${!MANIFEST_SCRIPT[@]}"; do
        if [[ "${MANIFEST_SCRIPT[$i]}" != */* ]]; then
            needs_search_dirs=1
            break
        fi
    done

    if (( needs_search_dirs == 0 )); then
        return 0
    fi

    if [[ ${#SCRIPT_SEARCH_DIRS[@]} -eq 0 ]]; then
        log ERROR "SCRIPT_SEARCH_DIRS is empty, but search-based entries are configured."
        exit 1
    fi

    for dir in "${SCRIPT_SEARCH_DIRS[@]}"; do
        if [[ -d "$dir" ]]; then
            (( ++valid ))
        fi
    done

    if (( valid == 0 )); then
        log ERROR "None of the configured script search directories exist!"
        log ERROR "Check your SCRIPT_SEARCH_DIRS configuration."
        exit 1
    fi

    return 0
}

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
    MANIFEST_INTERPRETER=()
    MANIFEST_INTERPRETER_ARGV_NAME=()

    for entry in "${UPDATE_SEQUENCE[@]}"; do
        [[ -z "${entry//[[:space:]]/}" ]] && continue
        if [[ "$entry" == *$'\n'* ]]; then
            printf 'Error: UPDATE_SEQUENCE entry must not contain newlines: %s\n' "$entry" >&2
            exit 1
        fi
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

        if [[ "$flags_part" == *$'\n'* || "$command_part" == *$'\n'* ]]; then
            printf 'Error: UPDATE_SEQUENCE fields must not contain newlines: %s\n' "$entry" >&2
            exit 1
        fi

        ignore_fail=false
        if [[ -n "$flags_part" ]]; then
            if [[ "$flags_part" == *","* ]]; then
                printf 'Error: Commas are not supported in UPDATE_SEQUENCE flags: %s\n' "$entry" >&2
                exit 1
            fi
            read -r -a flag_tokens <<< "$flags_part" || {
                printf 'Error: Malformed flags in UPDATE_SEQUENCE entry: %s\n' "$entry" >&2
                exit 1
            }
            for flag in "${flag_tokens[@]}"; do
                case "$flag" in
                    ignore-fail)
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
            *\'*|*\"*|*\\*|*$'\n'*)
                printf 'Error: UPDATE_SEQUENCE command field does not support quotes, backslash escapes, or newlines: %s\n' "$entry" >&2
                exit 1
                ;;
            *'|'*)
                printf 'Error: UPDATE_SEQUENCE command field must not contain "|": %s\n' "$entry" >&2
                exit 1
                ;;
        esac

        parts=()
        read -r -a parts <<< "$command_part" || {
            printf 'Error: Malformed command in UPDATE_SEQUENCE entry: %s\n' "$entry" >&2
            exit 1
        }

        ((${#parts[@]} > 0)) || {
            printf 'Error: Missing script in UPDATE_SEQUENCE entry: %s\n' "$entry" >&2
            exit 1
        }

        script="${parts[0]}"
        [[ -n "$script" ]] || {
            printf 'Error: Empty script name in UPDATE_SEQUENCE entry: %s\n' "$entry" >&2
            exit 1
        }
        if [[ "$script" == *$'\n'* ]]; then
            printf 'Error: Script name must not contain newline: %s\n' "$entry" >&2
            exit 1
        fi

        local argv_name="MANIFEST_ARGV_${idx}"
        declare -ga "$argv_name"
        local -n argv_ref="$argv_name"
        argv_ref=("${parts[@]:1}")

        local interp_argv_name="MANIFEST_INTERP_ARGV_${idx}"
        declare -ga "$interp_argv_name"
        local -n interp_argv_ref="$interp_argv_name"
        interp_argv_ref=()

        MANIFEST_MODE+=("$mode")
        MANIFEST_SCRIPT+=("$script")
        MANIFEST_IGNORE_FAIL+=("$ignore_fail")
        MANIFEST_ARGV_NAME+=("$argv_name")
        MANIFEST_PATH+=("")
        MANIFEST_PATH_STATE+=("unknown")
        MANIFEST_INTERPRETER+=("")
        MANIFEST_INTERPRETER_ARGV_NAME+=("$interp_argv_name")

        ((idx++)) || true
    done
}

resolve_and_validate_manifest() {
    local i=0 script="" script_path=""
    local -a matches=()
    local preflight_failures=0
    local needs_python=false

    log INFO "Performing pre-flight validation and conflict resolution..."

    for i in "${!MANIFEST_MODE[@]}"; do
        script="${MANIFEST_SCRIPT[$i]}"
        matches=()

        # Reuse cached resolution for repeated manifest entries.
        if [[ -n "${SCRIPT_RESOLUTION_CACHE["$script"]:-}" ]]; then
            script_path="${SCRIPT_RESOLUTION_CACHE["$script"]}"
            if [[ -f "$script_path" && -r "$script_path" ]]; then
                MANIFEST_PATH[$i]="$script_path"
                MANIFEST_PATH_STATE[$i]="ok"
                if [[ -n "${INTERPRETER_CACHE["$script_path"]:-}" ]]; then
                    MANIFEST_INTERPRETER[$i]="${INTERPRETER_CACHE["$script_path"]}"
                fi
                # Interpreter argv will be (re)resolved below; skip path scanning.
            else
                unset 'SCRIPT_RESOLUTION_CACHE["$script"]'
                script_path=""
            fi
        fi

        if [[ -z "$script_path" || "${MANIFEST_PATH_STATE[$i]}" != "ok" ]]; then
            matches=()
            # Step 1: Scan for paths with ~/ safety and deduplication.
            if [[ "$script" == */* ]]; then
                if [[ "${script:0:1}" == "~" && "$script" != "~/"* && "$script" != /* ]]; then
                    log ERROR "Unsupported home reference in script path: $script (only literal ~/ for your home is supported)"
                    MANIFEST_PATH[$i]="$script"
                    MANIFEST_PATH_STATE[$i]="missing"
                    ((preflight_failures++)) || true
                    continue
                fi
                normalize_explicit_script_path "$script" || {
                    log ERROR "Unsupported home reference in script path: $script (only literal ~/ for your home is supported)"
                    MANIFEST_PATH[$i]="$script"
                    MANIFEST_PATH_STATE[$i]="missing"
                    ((preflight_failures++)) || true
                    continue
                }
                local explicit_path="$REPLY"
                if [[ -f "$explicit_path" && -r "$explicit_path" ]]; then
                    matches+=("$explicit_path")
                fi
            else
                local dir=""
                local -A seen_canonical=()
                for dir in "${SCRIPT_SEARCH_DIRS[@]}"; do
                    local cand_path="${dir}/${script}"
                    if [[ -f "$cand_path" && -r "$cand_path" ]]; then
                        local canon="$cand_path"
                        canon="$(realpath -m -- "$cand_path" 2>/dev/null || printf '%s' "$cand_path")"
                        if [[ -z "${seen_canonical["$canon"]:-}" ]]; then
                            seen_canonical["$canon"]=1
                            matches+=("$cand_path")
                        fi
                    fi
                done
            fi

            # Step 2: Handle missing/duplicate scripts
            if ((${#matches[@]} == 0)); then
                MANIFEST_PATH[$i]="$script"
                MANIFEST_PATH_STATE[$i]="missing"
                log ERROR "Required script not found or unreadable: $script"
                ((preflight_failures++)) || true
                script_path=""
                continue
            elif ((${#matches[@]} == 1)); then
                script_path="${matches[0]}"
            else
                # CONFLICT RESOLUTION (no first-match-wins promise: prompt unless preconfigured)
                local predefined="${SCRIPT_CONFLICT_RESOLUTIONS[$script]:-}"
                if [[ -n "$predefined" ]]; then
                    if [[ "${predefined:0:1}" == "~" && "$predefined" != "~/"* && "$predefined" != /* ]]; then
                        log ERROR "Unsupported home reference in conflict resolution for '$script': $predefined (use literal ~/ or absolute/relative path)"
                        MANIFEST_PATH[$i]="$script"
                        MANIFEST_PATH_STATE[$i]="missing"
                        ((preflight_failures++)) || true
                        script_path=""
                        continue
                    fi
                    local explicit_pre="$predefined"
                    normalize_explicit_script_path "$predefined" && explicit_pre="$REPLY"
                    if [[ -f "$explicit_pre" && -r "$explicit_pre" ]]; then
                        script_path="$explicit_pre"
                        log INFO "Resolved duplicate '$script' using SCRIPT_CONFLICT_RESOLUTIONS -> $script_path"
                    else
                        log ERROR "Predefined resolution for '$script' is missing or unreadable: $explicit_pre"
                        MANIFEST_PATH[$i]="$script"
                        MANIFEST_PATH_STATE[$i]="missing"
                        ((preflight_failures++)) || true
                        script_path=""
                        continue
                    fi
                else
                    if [[ "$OPT_DRY_RUN" == true || "$OPT_FORCE" == true || ! -t 0 ]]; then
                        log ERROR "Conflict: Multiple versions of '$script' found."
                        local m
                        for m in "${matches[@]}"; do log ERROR "  Found at: $m"; done
                        log ERROR "Cannot prompt in non-interactive/dry-run/force mode. Add to SCRIPT_CONFLICT_RESOLUTIONS."
                        MANIFEST_PATH[$i]="$script"
                        MANIFEST_PATH_STATE[$i]="conflict"
                        ((preflight_failures++)) || true
                        script_path=""
                        continue
                    fi

                    printf '\n%s[CONFLICT DETECTED]%s Multiple versions of %s found:\n' "$CLR_YLW" "$CLR_RST" "$script"
                    local j
                    for ((j=0; j<${#matches[@]}; j++)); do
                        printf '  %d) %s\n' "$((j+1))" "${matches[$j]}"
                    done
                    local choice="" choice_dec=0
                    while true; do
                        if ! read -r -p "Which one should be executed? (1-${#matches[@]}): " choice; then
                            log ERROR "Input interrupted. Aborting."
                            exit 1
                        fi
                        if parse_decimal_choice "$choice" 1 "${#matches[@]}" choice_dec; then
                            script_path="${matches[$((choice_dec-1))]}"
                            log OK "Selected: $script_path"
                            log INFO "Tip: Add [\"$script\"]=\"$script_path\" to SCRIPT_CONFLICT_RESOLUTIONS to automate this."
                            break
                        fi
                        echo "Invalid choice. Please enter a number between 1 and ${#matches[@]}."
                    done
                fi
            fi

            MANIFEST_PATH[$i]="$script_path"
            MANIFEST_PATH_STATE[$i]="ok"
            SCRIPT_RESOLUTION_CACHE["$script"]="$script_path"
        else
            script_path="${MANIFEST_PATH[$i]}"
        fi

        # Step 3: Structural interpreter detection (no substring matching, no eval).
        local interp_argv_name="${MANIFEST_INTERPRETER_ARGV_NAME[$i]}"
        local -n interp_argv_ref="$interp_argv_name"
        interp_argv_ref=()
        local interp_display=""
        local interp_rc=0
        resolve_interpreter_for_script "$script_path" interp_display "$interp_argv_name" || interp_rc=$?
        if ((interp_rc == 2)); then
            MANIFEST_PATH_STATE[$i]="interpreter-conflict"
            MANIFEST_INTERPRETER[$i]=""
            log ERROR "Interpreter conflict for '$script': file extension and shebang disagree ($script_path)"
            HARD_FAILED_SCRIPTS+=("$script (interpreter-conflict)")
            ((preflight_failures++)) || true
            continue
        elif ((interp_rc != 0)); then
            MANIFEST_PATH_STATE[$i]="interpreter-unsupported"
            MANIFEST_INTERPRETER[$i]=""
            log ERROR "Unsupported or missing interpreter for '$script': $script_path (fix shebang/extension or install interpreter)"
            HARD_FAILED_SCRIPTS+=("$script (interpreter-unsupported)")
            ((preflight_failures++)) || true
            continue
        fi
        MANIFEST_INTERPRETER[$i]="$interp_display"
        INTERPRETER_CACHE["$script_path"]="$interp_display"
        # Track python need by inspecting resolved argv, not generic "python" substring.
        local _a=""
        for _a in "${interp_argv_ref[@]}"; do
            case "$(path_base "$_a")" in
                python|python3|python3.*) needs_python=true; break ;;
            esac
        done
    done

    if ((preflight_failures > 0)); then
        log ERROR "Aborting preflight due to ${preflight_failures} resolution error(s)"
        local _pf_idx
        for _pf_idx in "${!MANIFEST_PATH_STATE[@]}"; do
            local _pf_state="${MANIFEST_PATH_STATE[$_pf_idx]}"
            if [[ "$_pf_state" != "ok" ]]; then
                local _already=false _e=""
                for _e in "${HARD_FAILED_SCRIPTS[@]}"; do
                    [[ "$_e" == "${MANIFEST_SCRIPT[$_pf_idx]}"* ]] && _already=true && break
                done
                [[ "$_already" == true ]] || HARD_FAILED_SCRIPTS+=("${MANIFEST_SCRIPT[$_pf_idx]} ($_pf_state)")
            fi
        done
        return 1
    fi

    # Missing-interpreter failure is actionable; never implicit pacman install.
    if [[ "$needs_python" == true ]]; then
        local _has_py=false _a=""
        # Verify the actual selected python interpreters, not generic python.
        for i in "${!MANIFEST_INTERPRETER_ARGV_NAME[@]}"; do
            local -n _argv_ref="${MANIFEST_INTERPRETER_ARGV_NAME[$i]}"
            for _a in "${_argv_ref[@]:-}"; do
                case "$(path_base "$_a")" in
                    python|python3|python3.*)
                        if [[ "$_a" == /* ]]; then
                            [[ -x "$_a" ]] && _has_py=true
                        else
                            command -v "$_a" >/dev/null 2>&1 && _has_py=true
                        fi
                        ;;
                esac
            done
        done
        if [[ "$_has_py" == false ]] && ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
            log ERROR "Python interpreter required by UPDATE_SEQUENCE but not found. Install python (sudo pacman -S python) and re-run."
            return 1
        fi
    fi

    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "[DRY-RUN] Preflight validation is a pre-sync preview; post-sync scripts are unavailable until sync completes."
    else
        log OK "Preflight validation complete."
    fi
    return 0
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

    for cmd in sha256sum timeout mktemp find df du stat tee stdbuf mkfifo; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done

    if ((${#missing[@]} > 0)); then
        printf 'Error: Missing required commands: %s\n' "${missing[*]}" >&2
        printf 'Install with: sudo pacman -S bash coreutils findutils git\n' >&2
        exit 1
    fi
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

# Checked NUL-safe capture via private temp file with explicit producer status.
# Usage: capture_nul_records <out_array_name> <command...> ; rc=$?
# On producer failure returns producer rc and leaves array empty (fail closed).
capture_nul_records() {
    local out_name="$1"
    shift
    local -n out_ref="$out_name"
    local tmp="" rc=0 sz=0

    out_ref=()
    tmp="$(mktemp)" || return 1
    if "$@" >"$tmp" 2>/dev/null; then
        rc=0
    else
        rc=$?
        rm -f -- "$tmp" 2>/dev/null || true
        return "$rc"
    fi
    sz="$(stat -c '%s' -- "$tmp" 2>/dev/null || printf '0')"
    [[ "$sz" =~ ^[0-9]+$ ]] || sz=0
    if ((sz > 0)); then
        if ! tail -c1 -- "$tmp" 2>/dev/null | od -An -tx1 2>/dev/null | grep -q " 00"; then
            rm -f -- "$tmp" 2>/dev/null || true
            return 1
        fi
    fi
    mapfile -d '' -t out_ref <"$tmp" || {
        rm -f -- "$tmp" 2>/dev/null || true
        return 1
    }
    rm -f -- "$tmp" 2>/dev/null || true
    return 0
}

check_disk_space() {
    local path="$1"
    local tmp="" rc=0
    local -a lines=()
    local available_mb=0

    tmp="$(mktemp)" || return 1
    if df -BM --output=avail -- "$path" >"$tmp" 2>/dev/null; then
        rc=0
    else
        rc=$?
        rm -f -- "$tmp" 2>/dev/null || true
        log ERROR "Failed to query disk space for $path (df exit $rc)"
        return 1
    fi
    mapfile -t lines <"$tmp" || {
        rm -f -- "$tmp" 2>/dev/null || true
        return 1
    }
    rm -f -- "$tmp" 2>/dev/null || true
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
    local tmp="" rc=0
    local -a lines=()
    local available_bytes=0

    tmp="$(mktemp)" || { printf '0'; return 1; }
    if df -B1 --output=avail -- "$path" >"$tmp" 2>/dev/null; then
        rc=0
    else
        rc=$?
        rm -f -- "$tmp" 2>/dev/null || true
        printf '0'
        return "$rc"
    fi
    mapfile -t lines <"$tmp" || {
        rm -f -- "$tmp" 2>/dev/null || true
        printf '0'
        return 1
    }
    rm -f -- "$tmp" 2>/dev/null || true
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
    local is_interactive=false
    local script_payload=""

    # Interactive marker is read only from the script payload, never from
    # arbitrary arguments/binaries. Callers executing update scripts export
    # DUSKY_SCRIPT_PAYLOAD with the resolved script path; otherwise fall back
    # to no interactive bypass (log everything).
    if [[ -n "${DUSKY_SCRIPT_PAYLOAD:-}" && -f "${DUSKY_SCRIPT_PAYLOAD:-}" ]]; then
        script_payload="${DUSKY_SCRIPT_PAYLOAD}"
        if grep -q -i -E '^[[:space:]]*#[[:space:]]*dusky_interactive[[:space:]]*=[[:space:]]*(true|1)([^0-9a-zA-Z_]|$)' "$script_payload" 2>/dev/null; then
            is_interactive=true
        fi
    fi

    if [[ -z "$LOG_FILE" || ! -w "$LOG_FILE" ]]; then
        if (
            cd -- "$WORK_TREE" || exit 1
            "${cmd[@]}"
        ); then
            rc=0
        else
            rc=$?
        fi
        return "$rc"
    fi

    printf -v timestamp '%(%H:%M:%S)T' -1
    {
        printf '[%s] [SCRIPT ] BEGIN' "$timestamp"
        for arg in "${cmd[@]}"; do
            printf ' %q' "$arg"
        done
        printf '\n'
    } >> "$LOG_FILE" || printf 'Warning: failed to write BEGIN record to log\n' >&2

    if [[ "$is_interactive" == true ]]; then
        # Direct terminal, preserving TTY semantics, but BEGIN/END still logged.
        if (
            cd -- "$WORK_TREE" || exit 1
            "${cmd[@]}"
        ); then
            rc=0
        else
            rc=$?
        fi
        printf -v timestamp '%(%H:%M:%S)T' -1
        printf '[%s] [SCRIPT ] END rc=%d\n' "$timestamp" "$rc" >> "$LOG_FILE" || printf 'Warning: failed to write END record to log\n' >&2
        return "$rc"
    fi

    # Live streaming with explicitly managed logger processes and bounded cleanup.
    # No sleep guesses; no indefinite waits on daemon-held pipes.
    local fifo_out="" fifo_err="" tee_out_pid="" tee_err_pid=""
    local tee_out_rc=0 tee_err_rc=0 log_rc=0
    local use_stdbuf=false

    if command -v stdbuf >/dev/null 2>&1; then
        use_stdbuf=true
    fi

    fifo_out="$(mktemp -u)" || {
        if (
            cd -- "$WORK_TREE" || exit 1
            "${cmd[@]}"
        ); then rc=0; else rc=$?; fi
        printf -v timestamp '%(%H:%M:%S)T' -1
        printf '[%s] [SCRIPT ] END rc=%d (logging degraded: no fifo)\n' "$timestamp" "$rc" >> "$LOG_FILE" 2>/dev/null || true
        return "$rc"
    }
    fifo_err="$(mktemp -u)" || {
        rm -f -- "$fifo_out" 2>/dev/null || true
        if (
            cd -- "$WORK_TREE" || exit 1
            "${cmd[@]}"
        ); then rc=0; else rc=$?; fi
        printf -v timestamp '%(%H:%M:%S)T' -1
        printf '[%s] [SCRIPT ] END rc=%d (logging degraded: no fifo)\n' "$timestamp" "$rc" >> "$LOG_FILE" 2>/dev/null || true
        return "$rc"
    }
    if ! mkfifo -m 600 -- "$fifo_out" "$fifo_err" 2>/dev/null; then
        rm -f -- "$fifo_out" "$fifo_err" 2>/dev/null || true
        if (
            cd -- "$WORK_TREE" || exit 1
            "${cmd[@]}"
        ); then rc=0; else rc=$?; fi
        printf -v timestamp '%(%H:%M:%S)T' -1
        printf '[%s] [SCRIPT ] END rc=%d (logging degraded: mkfifo failed)\n' "$timestamp" "$rc" >> "$LOG_FILE" 2>/dev/null || true
        return "$rc"
    fi

    # Start loggers first (readers), unbuffered for prompt-without-newline visibility.
    if [[ "$use_stdbuf" == true ]]; then
        stdbuf -o0 -e0 tee -a -- "$LOG_FILE" <"$fifo_out" &
        tee_out_pid=$!
        stdbuf -o0 -e0 tee -a -- "$LOG_FILE" >&2 <"$fifo_err" &
        tee_err_pid=$!
    else
        tee -a -- "$LOG_FILE" <"$fifo_out" &
        tee_out_pid=$!
        tee -a -- "$LOG_FILE" >&2 <"$fifo_err" &
        tee_err_pid=$!
    fi
    ACTIVE_LOGGER_PIDS+=("$tee_out_pid" "$tee_err_pid")

    # Run payload with stdout/stderr to fifos; stdin inherits terminal for prompts.
    if (
        cd -- "$WORK_TREE" || exit 1
        "${cmd[@]}" >"$fifo_out" 2>"$fifo_err"
    ); then
        rc=0
    else
        rc=$?
    fi

    # Bounded logger drain: total ~5s for both loggers, then kill to avoid daemon-held-pipe hangs.
    # Concurrent wait (not sequential per-logger) to bound total cleanup.
    local _wait_iter=0 _pid="" _lrc=0 _out_timed_out=false _err_timed_out=false
    while (( _wait_iter < 50 )); do
        if ! kill -0 "$tee_out_pid" 2>/dev/null && ! kill -0 "$tee_err_pid" 2>/dev/null; then
            break
        fi
        sleep 0.1 2>/dev/null || sleep 1
        ((_wait_iter++)) || true
    done
    for _pid in "$tee_out_pid" "$tee_err_pid"; do
        if kill -0 "$_pid" 2>/dev/null; then
            kill "$_pid" 2>/dev/null || true
            log_rc=1
            if [[ "$_pid" == "$tee_out_pid" ]]; then _out_timed_out=true; else _err_timed_out=true; fi
            printf 'Warning: logger %s did not drain within 5s (daemon may hold pipe); killed to bound cleanup (payload rc=%d)\n' "$_pid" "$rc" >&2
        fi
    done
    for _pid in "$tee_out_pid" "$tee_err_pid"; do
        if wait "$_pid" 2>/dev/null; then
            : # logger ok
        else
            _lrc=$?
            # 143/SIGTERM from bounded kill is expected, not payload failure.
            if [[ "$_pid" == "$tee_out_pid" ]]; then
                tee_out_rc=$_lrc
                if [[ "$_out_timed_out" != true && $_lrc -ne 0 ]]; then
                    log_rc=1
                    printf 'Warning: stdout logger %s exited %d (payload rc=%d)\n' "$_pid" "$_lrc" "$rc" >&2
                fi
            else
                tee_err_rc=$_lrc
                if [[ "$_err_timed_out" != true && $_lrc -ne 0 ]]; then
                    log_rc=1
                    printf 'Warning: stderr logger %s exited %d (payload rc=%d)\n' "$_pid" "$_lrc" "$rc" >&2
                fi
            fi
        fi
    done
    # Remove reaped pids from tracking
    local _new_pids=() _p=""
    for _p in "${ACTIVE_LOGGER_PIDS[@]}"; do
        [[ "$_p" == "$tee_out_pid" || "$_p" == "$tee_err_pid" ]] || _new_pids+=("$_p")
    done
    ACTIVE_LOGGER_PIDS=("${_new_pids[@]}")
    rm -f -- "$fifo_out" "$fifo_err" 2>/dev/null || true

    printf -v timestamp '%(%H:%M:%S)T' -1
    if ((log_rc != 0 || tee_out_rc != 0 || tee_err_rc != 0)); then
        printf '[%s] [SCRIPT ] END rc=%d (logging issue out=%d err=%d, payload status retained)\n' "$timestamp" "$rc" "$tee_out_rc" "$tee_err_rc" >> "$LOG_FILE" 2>/dev/null || printf 'Warning: logging failure with payload rc=%d\n' "$rc" >&2
    else
        printf '[%s] [SCRIPT ] END rc=%d\n' "$timestamp" "$rc" >> "$LOG_FILE" 2>/dev/null || printf 'Warning: failed to write END record (payload rc=%d)\n' "$rc" >&2
    fi

    return "$rc"
}

reap_logging_processes() {
    local _p=""
    for _p in "${ACTIVE_LOGGER_PIDS[@]:-}"; do
        [[ -n "$_p" ]] || continue
        if kill -0 "$_p" 2>/dev/null; then
            kill "$_p" 2>/dev/null || true
        fi
    done
    # Reap to avoid zombies; bounded (no indefinite wait).
    local _i=0
    for _p in "${ACTIVE_LOGGER_PIDS[@]:-}"; do
        _i=0
        while kill -0 "$_p" 2>/dev/null && ((_i < 10)); do
            sleep 0.1 2>/dev/null || sleep 1
            ((_i++)) || true
        done
        wait "$_p" 2>/dev/null || true
    done
    ACTIVE_LOGGER_PIDS=()
}

auto_prune() {
    if [[ -d "$ACTIVE_LOG_BASE_DIR" ]]; then
        find "$ACTIVE_LOG_BASE_DIR" -type f -name 'dusky_update_*.log' -mtime "+${LOG_RETENTION_DAYS}" -delete \
            2>/dev/null || true
    fi

    # Never age-prune unresolved recovery data. Only disposable logs are pruned
    # above; collision backups, manual-merge directories, and user/history
    # snapshots are preserved until explicitly resolved by the user.
    if [[ -d "$ACTIVE_BACKUP_BASE_DIR" ]]; then
        log INFO "Preserving recovery backups (needs_merge_*, untracked_collisions_*, user_mods_*, repo_history_*, pre_reset_*) until manually resolved."
    fi
}

# ==============================================================================
# GIT HELPERS
# ==============================================================================
# Git's own internal locking is authoritative. The updater never scans /proc,
# never ages or deletes Git lockfiles, and never implements single-instance
# guards. A leftover *.lock only produces an actionable diagnostic; Git
# operation failures propagate and must be resolved manually.
detect_git_lock_state() {
    local lock_name=""

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

    printf 'none'
}

get_repo_state() {
    local lock_state=""

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

    # Incomplete first-install marker left by a failed checkout: never misread
    # an incomplete clone as a healthy repository.
    if [[ -e "${DOTFILES_GIT_DIR}/.dusky_checkout_incomplete" ]]; then
        log ERROR "Previous first-time checkout did not complete (marker present). Resolve manually or remove the incomplete repository and re-run."
        REPLY="invalid"
        return 0
    fi

    lock_state="$(detect_git_lock_state)"
    if [[ "$lock_state" != "none" ]]; then
        log ERROR "Git lock present: ${DOTFILES_GIT_DIR}/${lock_state}"
        log ERROR "Another Git process may be running, or a previous Git command was interrupted."
        log ERROR "The updater will not remove Git lockfiles automatically. Resolve manually (e.g. wait, then inspect with git status) and re-run."
        REPLY="invalid"
        return 0
    fi

    if ! "${GIT_CMD[@]}" rev-parse --git-dir >/dev/null 2>&1; then
        log ERROR "Repository metadata is invalid or corrupted: $DOTFILES_GIT_DIR"
        log ERROR "Git said rev-parse --git-dir failed; inspect manually and do not delete lockfiles blindly."
        REPLY="invalid"
        return 0
    fi

    REPLY="valid"
    return 0
}

ensure_repo_defaults() {
    local current_value="" rc=0

    current_value="$("${GIT_CMD[@]}" config get status.showUntrackedFiles 2>/dev/null)" || rc=$?
    if ((rc != 0)); then
        # get returns non-zero when unset; treat empty as needing default.
        current_value=""
    fi
    if [[ "$current_value" != "no" ]]; then
        if [[ "$OPT_DRY_RUN" == true ]]; then
            log INFO "[DRY-RUN] Would set git config: status.showUntrackedFiles=no"
        else
            if ! "${GIT_CMD[@]}" config set status.showUntrackedFiles no >/dev/null 2>&1; then
                log ERROR "Failed to set git config status.showUntrackedFiles=no"
                return 1
            fi
        fi
    fi
    return 0
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
    local rc=0

    expected_url="$(canonicalize_git_remote_url "$REPO_URL")"

    for active_remote in origin "$UPSTREAM_REMOTE"; do
        current_url="" rc=0
        current_url="$("${GIT_CMD[@]}" remote get-url "$active_remote" 2>/dev/null)" || rc=$?
        if ((rc == 0)) && [[ -n "$current_url" && "$(canonicalize_git_remote_url "$current_url")" == "$expected_url" ]]; then
            REPLY="$active_remote"
            return 0
        fi
    done

    current_url="" rc=0
    current_url="$("${GIT_CMD[@]}" remote get-url "$UPSTREAM_REMOTE" 2>/dev/null)" || rc=$?
    if ((rc == 0)) && [[ -n "$current_url" ]]; then
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
    local find_tmp="" find_rc=0

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
        find_tmp="$(mktemp)" || return 1
        if find "$abs" -mindepth 1 -maxdepth 1 -printf '%P\0' >"$find_tmp" 2>/dev/null; then
            find_rc=0
        else
            find_rc=$?
            rm -f -- "$find_tmp" 2>/dev/null || true
            log ERROR "Failed to enumerate directory for collision check: $(quote_for_log "$rel") (find exit $find_rc)"
            return 1
        fi
        while IFS= read -r -d '' child; do
            children+=("$child")
        done <"$find_tmp" || true
        rm -f -- "$find_tmp" 2>/dev/null || true

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

# Reject unsupported index states before any mutation. Preserves staged-only
# content by requiring a Git history backup first; unmerged, assume-unchanged,
# and skip-worktree states are rejected safely before moving collisions.
check_index_supported_state() {
    local tmp="" rc=0
    local -a unmerged=()
    local -a verbose_records=()
    local rec="" tag=""

    tmp="$(mktemp)" || return 1
    if "${GIT_CMD[@]}" ls-files -u -z >"$tmp" 2>/dev/null; then
        rc=0
    else
        rc=$?
        rm -f -- "$tmp" 2>/dev/null || true
        log ERROR "Failed to inspect index for unmerged entries (git ls-files -u exit $rc)"
        return 1
    fi
    mapfile -d '' -t unmerged <"$tmp" || {
        rm -f -- "$tmp" 2>/dev/null || true
        return 1
    }
    rm -f -- "$tmp" 2>/dev/null || true
    if ((${#unmerged[@]} > 0)); then
        log ERROR "Unmerged index entries present; resolve manually before updating (git status)."
        return 1
    fi

    tmp="$(mktemp)" || return 1
    if "${GIT_CMD[@]}" ls-files -v -z >"$tmp" 2>/dev/null; then
        rc=0
    else
        rc=$?
        rm -f -- "$tmp" 2>/dev/null || true
        log ERROR "Failed to inspect index flags (git ls-files -v -z exit $rc)"
        return 1
    fi
    mapfile -d '' -t verbose_records <"$tmp" || {
        rm -f -- "$tmp" 2>/dev/null || true
        log ERROR "Failed to parse index flag records."
        return 1
    }
    rm -f -- "$tmp" 2>/dev/null || true
    for rec in "${verbose_records[@]}"; do
        [[ -n "$rec" ]] || continue
        tag="${rec:0:1}"
        # Reject skip-worktree (S/s) and any assume-unchanged lowercase tag (h/s/etc.).
        # Normal cached entries are uppercase H (and related uppercase); lowercase
        # indicates assume-unchanged, S/s indicates skip-worktree (s = both).
        if [[ "$tag" == "S" || "$tag" == "s" || "$tag" =~ ^[a-z]$ ]]; then
            log ERROR "assume-unchanged/skip-worktree entry present (${tag}); unset (git update-index --no-assume-unchanged/--no-skip-worktree) or resolve manually before updating."
            return 1
        fi
    done
    return 0
}

# Reject repository/backup/log overlap with incoming paths before moves/checkout.
# Also rejects symlink-ancestor traversal that would move/copy outside worktree.
check_incoming_overlap_and_symlinks() {
    local -a incoming=("$@")
    local rel="" abs="" ancestor=""
    local -a sensitive_rels=()
    local sens="" s_abs=""

    # Sensitive relative paths (relative to WORK_TREE).
    sensitive_rels+=("dusky")
    # Derive backup/log relatives when they live under WORK_TREE.
    case "$ACTIVE_BACKUP_BASE_DIR" in
        "$WORK_TREE"/*) sensitive_rels+=("${ACTIVE_BACKUP_BASE_DIR#"$WORK_TREE"/}") ;;
    esac
    case "$ACTIVE_LOG_BASE_DIR" in
        "$WORK_TREE"/*) sensitive_rels+=("${ACTIVE_LOG_BASE_DIR#"$WORK_TREE"/}") ;;
    esac
    if [[ -n "$LOG_FILE" ]]; then
        case "$LOG_FILE" in
            "$WORK_TREE"/*) sensitive_rels+=("${LOG_FILE#"$WORK_TREE"/}") ;;
        esac
    fi
    for sens in "$USER_MODS_BACKUP_DIR" "$FULL_TRACKED_BACKUP_DIR" "$GIT_HISTORY_BACKUP_DIR" "$MERGE_DIR"; do
        [[ -n "$sens" ]] || continue
        case "$sens" in
            "$WORK_TREE"/*) sensitive_rels+=("${sens#"$WORK_TREE"/}") ;;
        esac
    done
    for coll in "${COLLISION_BACKUP_DIRS[@]}"; do
        case "$coll" in
            "$WORK_TREE"/*) sensitive_rels+=("${coll#"$WORK_TREE"/}") ;;
        esac
    done

    for rel in "${incoming[@]}"; do
        [[ -n "$rel" ]] || continue
        is_safe_worktree_relative "$rel" || {
            log ERROR "Unsafe incoming path (absolute, .., or newline): $(quote_for_log "$rel")"
            return 1
        }
        # Overlap: incoming equals, is ancestor of, or is descendant of sensitive.
        for sens in "${sensitive_rels[@]}"; do
            [[ -n "$sens" ]] || continue
            if [[ "$rel" == "$sens" || "$rel" == "$sens"/* || "$sens" == "$rel"/* ]]; then
                log ERROR "Incoming path overlaps backup/log/repo location: $(quote_for_log "$rel") vs $(quote_for_log "$sens"). Aborting to protect recovery data."
                return 1
            fi
        done
        # Symlink-ancestor traversal: any existing ancestor that is symlink or non-dir blocks move.
        abs="${WORK_TREE}/${rel}"
        ancestor="$rel"
        while [[ "$ancestor" == */* ]]; do
            ancestor="${ancestor%/*}"
            s_abs="${WORK_TREE}/${ancestor}"
            if [[ -L "$s_abs" ]]; then
                log ERROR "Incoming path traverses symlink ancestor: $(quote_for_log "$rel") via $(quote_for_log "$ancestor"). Aborting to avoid moving data outside worktree."
                return 1
            fi
            if [[ -e "$s_abs" && ! -d "$s_abs" ]]; then
                # File ancestor is a collision itself; will be handled as root, but
                # do not silently follow it here.
                break
            fi
        done
        # Target below symlink must not move/copy outside worktree.
        if has_symlink_ancestor "$abs" 2>/dev/null; then
            # Only fail if the symlink ancestor is an existing live path that
            # would redirect the move outside; collision roots handle file ancestors.
            # has_symlink_ancestor checks all ancestors; treat any hit as unsafe
            # unless the symlink itself is a tracked collision root to be moved.
            # Conservative: abort and require manual review.
            log ERROR "Target has symlink ancestor, refusing to move/copy outside worktree: $(quote_for_log "$rel")"
            return 1
        fi
    done
    return 0
}

rollback_collision_moves() {
    local backup_dir="" manifest_nul="" intent_nul="" rel="" src="" dest=""
    local -a planned=() committed=()
    local -A to_rollback=()
    local failures=0 incomplete=0

    for backup_dir in "${COLLISION_BACKUP_DIRS[@]}"; do
        [[ -d "$backup_dir" ]] || { ((incomplete++)) || true; continue; }
        manifest_nul="${backup_dir}/meta/MOVED_PATHS.nul"
        intent_nul="${backup_dir}/meta/INTENT.nul"
        planned=(); committed=()
        to_rollback=()
        if [[ -f "$intent_nul" ]]; then
            mapfile -d '' -t planned <"$intent_nul" 2>/dev/null || { log ERROR "Rollback: cannot read intent manifest $intent_nul"; ((failures++)) || true; continue; }
        else
            log ERROR "Rollback: missing intent manifest $intent_nul; cannot verify completeness."
            ((failures++)) || true
        fi
        if [[ -f "$manifest_nul" ]]; then
            mapfile -d '' -t committed <"$manifest_nul" 2>/dev/null || { log ERROR "Rollback: cannot read committed manifest $manifest_nul"; ((failures++)) || true; continue; }
        else
            log ERROR "Rollback: missing committed manifest $manifest_nul."
            ((failures++)) || true
        fi
        for rel in "${planned[@]}" "${committed[@]}"; do
            [[ -n "$rel" ]] || continue
            to_rollback["$rel"]=1
        done
        # Also include in-memory moved paths (covers journal-write failure where
        # manifest append failed but mv succeeded and intent may be partial).
        for rel in "${!COLLISION_MOVED_PATHS[@]}"; do
            # Only for this backup dir? In-memory is global; check payload exists in this dir.
            src="${backup_dir}/payload/${rel}"
            if [[ -e "$src" || -L "$src" ]]; then
                to_rollback["$rel"]=1
            fi
        done
        if ((${#to_rollback[@]} == 0)); then
            continue
        fi
        for rel in "${!to_rollback[@]}"; do
            [[ -n "$rel" ]] || continue
            src="${backup_dir}/payload/${rel}"
            dest="${WORK_TREE}/${rel}"
            if [[ ! -e "$src" && ! -L "$src" ]]; then
                log ERROR "Rollback incomplete: missing payload $src for $(quote_for_log "$rel")"
                ((failures++)) || true
                continue
            fi
            # Occupied destination is not successful rollback.
            if path_exists "$dest"; then
                log ERROR "Rollback incomplete: destination occupied for $(quote_for_log "$rel") (preserving both)"
                ((failures++)) || true
                continue
            fi
            local -A _cache=()
            if ! ensure_relative_parent_dir "$WORK_TREE" "$rel" _cache; then
                log ERROR "Rollback incomplete: cannot recreate parent for $(quote_for_log "$rel")"
                ((failures++)) || true
                continue
            fi
            if ! validate_restore_parent_topology "$dest"; then
                log ERROR "Rollback incomplete: unsafe parent for $(quote_for_log "$rel")"
                ((failures++)) || true
                continue
            fi
            if mv -T -- "$src" "$dest" 2>/dev/null; then
                unset 'COLLISION_MOVED_PATHS["$rel"]'
            else
                log ERROR "Rollback incomplete: mv failed for $(quote_for_log "$rel")"
                ((failures++)) || true
            fi
        done
    done
    if ((failures > 0 || incomplete > 0)); then
        log ERROR "Collision rollback incomplete (failures=$failures incomplete_dirs=$incomplete); recovery material preserved."
        return 1
    fi
    return 0
}

backup_worktree_collisions_for_ref() {
    local ref="$1"
    local honor_current_tracked="${2:-true}"

    local target_path="" abs="" ancestor="" remaining="" part=""
    local coll_backup_dir="" coll_rel="" coll_src="" coll_dest=""
    local coll_manifest_nul="" coll_intent_nul="" info_file="" payload_root="" meta_root=""
    local tracked_path=""
    local required_bytes=0
    local path_bytes=0
    local skip=false
    local -A collision_candidates=()
    local -A collision_roots=()
    local -A mkdir_cache=()
    local -A payload_mkdir_cache=()
    local -A current_tracked_exact=()
    local -A current_tracked_descendants=()
    local -a ls_tree_records=()
    local ls_rc=0 tree_rc=0
    local tmp_ls="" tmp_tree=""

    # Fail closed on Git inventories: checked NUL-safe capture with explicit status.
    if [[ "$honor_current_tracked" == "true" ]]; then
        tmp_ls="$(mktemp)" || { log ERROR "Failed to allocate temp file for ls-files"; return 1; }
        if "${GIT_CMD[@]}" ls-files -z >"$tmp_ls" 2>/dev/null; then
            ls_rc=0
        else
            ls_rc=$?
            rm -f -- "$tmp_ls" 2>/dev/null || true
            log ERROR "Failed to enumerate current tracked files (git ls-files exit $ls_rc). Aborting before reset to avoid misreading failure as clean tree."
            return 1
        fi
        while IFS= read -r -d '' tracked_path; do
            # Reject malformed/incomplete records: empty entries besides terminator are skipped,
            # but a completely empty output with non-zero producer already failed above.
            [[ -n "$tracked_path" ]] || continue
            if [[ "$tracked_path" == *$'\n'* ]]; then
                # Newlines are legal in filenames; NUL-delimited read preserves them, so keep.
                :
            fi
            current_tracked_exact["$tracked_path"]=1
            ancestor="$tracked_path"
            while [[ "$ancestor" == */* ]]; do
                ancestor="${ancestor%/*}"
                current_tracked_descendants["$ancestor"]=1
            done
        done <"$tmp_ls" || true
        rm -f -- "$tmp_ls" 2>/dev/null || true
    fi

    tmp_tree="$(mktemp)" || { log ERROR "Failed to allocate temp file for ls-tree"; return 1; }
    if "${GIT_CMD[@]}" ls-tree -r -z --name-only "$ref" >"$tmp_tree" 2>/dev/null; then
        tree_rc=0
    else
        tree_rc=$?
        rm -f -- "$tmp_tree" 2>/dev/null || true
        log ERROR "Failed to enumerate incoming ref $ref (git ls-tree exit $tree_rc). Aborting before reset."
        return 1
    fi
    ls_tree_records=()
    while IFS= read -r -d '' target_path; do
        [[ -n "$target_path" ]] || continue
        ls_tree_records+=("$target_path")
    done <"$tmp_tree" || true
    rm -f -- "$tmp_tree" 2>/dev/null || true
    if ((${#ls_tree_records[@]} == 0)); then
        # Distinguish empty tree (valid, e.g. empty repo) from failed read:
        # producer succeeded, so empty is allowed; just return (no collisions).
        :
    fi

    # Overlap check before any move: incoming must not replace backup/log/repo.
    if ((${#ls_tree_records[@]} > 0)); then
        check_incoming_overlap_and_symlinks "${ls_tree_records[@]}" || return 1
    fi

    for target_path in "${ls_tree_records[@]}"; do
        [[ -n "$target_path" ]] || continue

        abs="${WORK_TREE}/${target_path}"
        if path_exists "$abs"; then
            if [[ -d "$abs" && ! -L "$abs" ]]; then
                if [[ "$honor_current_tracked" == "true" && -n "${current_tracked_descendants["$target_path"]+_}" ]]; then
                    collect_dir_collision_roots "$target_path" current_tracked_exact current_tracked_descendants collision_candidates || {
                        log ERROR "Collision enumeration failed for directory: $(quote_for_log "$target_path")"
                        return 1
                    }
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
    done

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
    # Register partial backup early for reporting; completion flagged only on success.
    COLLISION_BACKUP_DIRS+=("$coll_backup_dir")
    payload_root="${coll_backup_dir}/payload"
    meta_root="${coll_backup_dir}/meta"
    mkdir -p -- "$payload_root" "$meta_root" || {
        log ERROR "Failed to create collision payload/meta subdirectories"
        return 1
    }
    chmod 700 -- "$payload_root" "$meta_root" 2>/dev/null || true

    coll_manifest_nul="${meta_root}/MOVED_PATHS.nul"
    coll_intent_nul="${meta_root}/INTENT.nul"
    info_file="${meta_root}/INFO.txt"

    : > "$coll_manifest_nul" || {
        log ERROR "Failed to create collision manifest"
        return 1
    }
    chmod 600 -- "$coll_manifest_nul" 2>/dev/null || true
    : > "$coll_intent_nul" || {
        log ERROR "Failed to create collision intent record"
        return 1
    }
    chmod 600 -- "$coll_intent_nul" 2>/dev/null || true
    # Durable intent BEFORE any mutation: all planned roots, so rollback covers
    # completed moves even if per-move journal append later fails.
    for coll_rel in "${!collision_roots[@]}"; do
        printf '%s\0' "$coll_rel" >> "$coll_intent_nul" || {
            log ERROR "Failed to write collision intent record"
            return 1
        }
    done

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

        # Ancestor inspection before following paths: refuse symlink ancestors.
        if has_symlink_ancestor "$coll_src"; then
            log ERROR "Refusing to move collision with symlink ancestor (would leave worktree): $(quote_for_log "$coll_rel")"
            # Roll back completed moves where safe, preserve recovery material.
            rollback_collision_moves || true
            return 1
        fi

        coll_dest="${payload_root}/${coll_rel}"
        ensure_relative_parent_dir "$payload_root" "$coll_rel" payload_mkdir_cache || {
            log ERROR "Failed to create collision backup directory for: $(quote_for_log "$coll_rel")"
            rollback_collision_moves || true
            return 1
        }
        # Validate parent topology before move (no symlink traversal).
        if ! validate_restore_parent_topology "$coll_src"; then
            # Source parent already exists; this check is for destination parent.
            :
        fi
        local dest_parent
        dest_parent="$(path_parent "$coll_dest")"
        if [[ -L "$dest_parent" ]]; then
            log ERROR "Refusing to move collision into symlinked backup parent: $(quote_for_log "$coll_rel")"
            rollback_collision_moves || true
            return 1
        fi

        if ! mv -T -- "$coll_src" "$coll_dest"; then
            log ERROR "Failed to move colliding path: $(quote_for_log "$coll_rel")"
            rollback_collision_moves || true
            return 1
        fi

        if ! printf '%s\0' "$coll_rel" >> "$coll_manifest_nul"; then
            log ERROR "Failed to record colliding path: $(quote_for_log "$coll_rel") (move already committed; intent preserves it for rollback)"
            rollback_collision_moves || true
            return 1
        fi
        COLLISION_MOVED_PATHS["$coll_rel"]=1

        log RAW "  → Backed up collision: $(quote_for_log "$coll_rel")"
    done

    log OK "Collisions backed up to: $coll_backup_dir (payload/, meta/MOVED_PATHS.nul)"
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
        else
            rc=$?
        fi

        if (( attempt < FETCH_MAX_ATTEMPTS )); then
            if (( rc == 124 )); then
                log WARN "Fetch attempt $attempt/$FETCH_MAX_ATTEMPTS timed out (exit 124). Retrying in ${wait_time}s..."
            else
                log WARN "Fetch attempt $attempt/$FETCH_MAX_ATTEMPTS failed (exit $rc). Retrying in ${wait_time}s..."
            fi
            sleep "$wait_time"
            (( wait_time *= 2 ))
        fi
        (( attempt++ ))
    done

    if (( rc == 124 )); then
        log ERROR "Fetch failed after $FETCH_MAX_ATTEMPTS attempts due to repeated timeouts (exit 124)"
    else
        log ERROR "Fetch failed after $FETCH_MAX_ATTEMPTS attempts (last exit $rc)"
    fi
    return 1
}

clone_into_dir_with_retry() {
    local dest_dir="${1:?missing clone dest}"
    local -i attempt=1
    local -i wait_time=$FETCH_INITIAL_BACKOFF
    local -i rc=0

    while (( attempt <= FETCH_MAX_ATTEMPTS )); do
        if timeout "${CLONE_TIMEOUT}s" \
            "$GIT_BIN" clone --bare --branch "$BRANCH" "$REPO_URL" "$dest_dir" \
            >> "$LOG_FILE" 2>&1; then
            # Ensure the cloned repository has the standard remote-tracking refspec configured
            if ! "$GIT_BIN" --git-dir="$dest_dir" config set remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*" >> "$LOG_FILE" 2>&1; then
                log ERROR "Clone succeeded but failed to configure remote.origin.fetch"
                return 1
            fi
            return 0
        else
            rc=$?
        fi

        # Never delete the configured repo path; only clean the private temp sibling.
        rm -rf -- "$dest_dir" 2>/dev/null || true

        if (( attempt < FETCH_MAX_ATTEMPTS )); then
            if (( rc == 124 )); then
                log WARN "Clone attempt $attempt/$FETCH_MAX_ATTEMPTS timed out (exit 124). Retrying in ${wait_time}s..."
            else
                log WARN "Clone attempt $attempt/$FETCH_MAX_ATTEMPTS failed (exit $rc). Retrying in ${wait_time}s..."
            fi
            sleep "$wait_time"
            (( wait_time *= 2 ))
        fi

        (( attempt++ ))
    done

    if (( rc == 124 )); then
        log ERROR "Clone failed after $FETCH_MAX_ATTEMPTS attempts due to repeated timeouts (exit 124)"
    else
        log ERROR "Clone failed after $FETCH_MAX_ATTEMPTS attempts (last exit $rc)"
    fi
    return 1
}

clone_with_retry() {
    # Legacy wrapper: clone into a private temp sibling and publish atomically.
    local tmp_parent="" tmp_dest="" publish_rc=0
    tmp_parent="$(path_parent "$DOTFILES_GIT_DIR")"
    [[ -d "$tmp_parent" && -w "$tmp_parent" && -O "$tmp_parent" ]] || {
        log ERROR "Cannot clone: parent of $DOTFILES_GIT_DIR is not a writable owned directory"
        return 1
    }
    tmp_dest="$(mktemp -d -p "$tmp_parent" ".dusky_clone_tmp.XXXXXX")" || {
        log ERROR "Failed to create private clone staging directory"
        return 1
    }
    chmod 700 -- "$tmp_dest" 2>/dev/null || true
    rmdir -- "$tmp_dest" 2>/dev/null || {
        log ERROR "Failed to prepare private clone staging directory"
        return 1
    }
    if ! clone_into_dir_with_retry "$tmp_dest"; then
        rm -rf -- "$tmp_dest" 2>/dev/null || true
        return 1
    fi
    if [[ -e "$DOTFILES_GIT_DIR" || -L "$DOTFILES_GIT_DIR" ]]; then
        log ERROR "Clone destination already exists, refusing to overwrite: $DOTFILES_GIT_DIR"
        rm -rf -- "$tmp_dest" 2>/dev/null || true
        return 1
    fi
    if mv -T -- "$tmp_dest" "$DOTFILES_GIT_DIR"; then
        return 0
    else
        publish_rc=$?
        log ERROR "Failed to publish cloned repository (mv exit $publish_rc)"
        rm -rf -- "$tmp_dest" 2>/dev/null || true
        return 1
    fi
}

show_update_preview() {
    local local_head="$1"
    local remote_head="$2"
    local base_commit="${3:-}"
    local diff_base="" commit_count="?" commit_rc=0 diff_rc=0
    local -a changed_files=()
    local tmp=""

    diff_base="$local_head"
    [[ -n "$base_commit" ]] && diff_base="$base_commit"

    commit_count="$("${GIT_CMD[@]}" rev-list --count "${local_head}..${remote_head}" 2>/dev/null)" || commit_rc=$?
    if ((commit_rc != 0)); then
        commit_count="?"
        log WARN "Failed to count upstream commits (git rev-list exit $commit_rc); showing cached-ref preview."
    fi
    tmp="$(mktemp)" || return 1
    if "${GIT_CMD[@]}" diff -z --name-only "${diff_base}..${remote_head}" >"$tmp" 2>/dev/null; then
        diff_rc=0
    else
        diff_rc=$?
        rm -f -- "$tmp" 2>/dev/null || true
        log WARN "Failed to enumerate changed files (git diff exit $diff_rc); showing cached-ref preview."
        changed_files=()
    fi
    if ((diff_rc == 0)); then
        mapfile -d '' -t changed_files <"$tmp" || {
            rm -f -- "$tmp" 2>/dev/null || true
            return 1
        }
        rm -f -- "$tmp" 2>/dev/null || true
    fi

    printf '\n'
    log INFO "Upstream changes (cached-ref preview: $remote_head):"
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
    # Returns 0 with "mode\t oid" on exact match, 10 for legitimate missing,
    # 1 for any metadata failure (producer failure, malformed, partial, ambiguous).
    # Uses literal pathspec so '*'/'[' never glob to another path.
    local path="$1"
    local tmp="" rc=0 sz=0
    local -a records=()
    local record="" meta="" ret_path="" mode="" type="" oid=""

    tmp="$(mktemp)" || { printf ''; return 1; }
    if "${GIT_CMD[@]}" ls-tree -z HEAD -- ":(literal)$path" >"$tmp" 2>/dev/null; then
        rc=0
    else
        rc=$?
        rm -f -- "$tmp" 2>/dev/null || true
        printf ''
        return 1
    fi
    sz="$(stat -c '%s' -- "$tmp" 2>/dev/null || printf '0')"
    [[ "$sz" =~ ^[0-9]+$ ]] || sz=0
    if ((sz == 0)); then
        rm -f -- "$tmp" 2>/dev/null || true
        printf ''
        return 10
    fi
    # Complete framing: non-empty -z output must end with NUL.
    if ! tail -c1 -- "$tmp" 2>/dev/null | od -An -tx1 2>/dev/null | grep -q " 00"; then
        rm -f -- "$tmp" 2>/dev/null || true
        printf ''
        return 1
    fi
    mapfile -d '' -t records <"$tmp" 2>/dev/null || {
        rm -f -- "$tmp" 2>/dev/null || true
        printf ''
        return 1
    }
    rm -f -- "$tmp" 2>/dev/null || true
    if ((${#records[@]} == 0)); then
        printf ''
        return 10
    fi
    if ((${#records[@]} != 1)); then
        printf ''
        return 1
    fi
    record="${records[0]}"
    case "$record" in
        *$'\t'*) ;;
        *) printf ''; return 1 ;;
    esac
    meta="${record%%$'\t'*}"
    ret_path="${record#*$'\t'}"
    [[ "$ret_path" == "$path" ]] || { printf ''; return 1; }
    read -r mode type oid <<< "$meta" || { printf ''; return 1; }
    [[ "$mode" =~ ^[0-9]+$ ]] || { printf ''; return 1; }
    [[ "$type" == "blob" || "$type" == "tree" || "$type" == "commit" ]] || { printf ''; return 1; }
    is_hex_oid "$oid" || { printf ''; return 1; }
    printf '%s\t%s' "$mode" "$oid"
    return 0
}

handle_unrelated_upstream_history() {
    local remote_oid="${1:?missing remote OID}"
    local sync_choice="1"
    local sync_dec=0
    local reset_rc=0

    log WARN "Local repository does not share history with upstream ($remote_oid)."

    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "[DRY-RUN] Would back up the current tracked tree and Git history, then reset to $remote_oid (preview only, no changes)"
        return 0
    fi

    # --force never authorizes diverged/unrelated reset; require explicit flag.
    if [[ -t 0 && "$OPT_FORCE" != true ]]; then
        printf '\n%s[UNRELATED HISTORY]%s The existing bare repo at %s is not based on Dusky upstream.\n' \
            "$CLR_YLW" "$CLR_RST" "$DOTFILES_GIT_DIR"
        printf '  1) Abort (keep current state) [DEFAULT]\n'
        printf '  %s2) Replace local repo contents with upstream [RECOMMENDED]%s\n' \
            "$CLR_GRN" "$CLR_RST"
        printf '     Current tracked files and Git history will be backed up before reset.\n\n'

        if ! read -r -t "$PROMPT_TIMEOUT_LONG" -p "Choice [1-2] (default: 1): " sync_choice; then
            sync_choice="1"
        fi
        sync_choice="${sync_choice:-1}"
        parse_decimal_choice "$sync_choice" 1 2 sync_dec || sync_dec=1
        sync_choice="$sync_dec"
    elif [[ "$OPT_ALLOW_DIVERGED_RESET" == true ]]; then
        sync_choice="2"
    else
        log ERROR "Unattended/force mode and unrelated history. Aborting to prevent data loss (use --allow-diverged-reset to override)."
        return "$SYNC_RC_RECOVERABLE"
    fi

    case "$sync_choice" in
        1)
            log INFO "Aborted by user."
            return "$SYNC_RC_RECOVERABLE"
            ;;
        2)
            check_index_supported_state || return "$SYNC_RC_UNSAFE"
            backup_git_history || return "$SYNC_RC_UNSAFE"
            backup_full_tracked_tree || return "$SYNC_RC_UNSAFE"
            if ! backup_worktree_collisions_for_ref "$remote_oid" true; then
                log ERROR "Collision backup failed; aborting before reset."
                return "$SYNC_RC_UNSAFE"
            fi

            log INFO "Resetting to ${remote_oid}..."
            reset_rc=0
            if "${GIT_CMD[@]}" reset --hard "$remote_oid" >> "$LOG_FILE" 2>&1; then
                log OK "Reset complete."
                log WARN "Previous tracked files were preserved in a full backup and were not auto-restored because the histories are unrelated."
                log INFO "Review the preserved backup at: $FULL_TRACKED_BACKUP_DIR"
            else
                reset_rc=$?
                log ERROR "Reset to $remote_oid failed (exit $reset_rc). See $LOG_FILE."
                if ! rollback_collision_moves; then
                    log ERROR "Collision rollback incomplete after unrelated reset failure."
                fi
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
    local tmp="" rc=0

    CHANGE_PATHS=()
    CHANGE_STATUS=()
    CHANGE_OLD_MODE=()
    CHANGE_OLD_OID=()
    CHANGE_BACKUP_HAS_FILE=()

    # Dry-run is read-only: never refresh the index.
    if [[ "$OPT_DRY_RUN" != true ]]; then
        if "${GIT_CMD[@]}" update-index -q --refresh >/dev/null 2>&1; then
            rc=0
        else
            rc=$?
            if ((rc == 1)); then
                log INFO "git update-index --refresh reported dirty index (exit 1); proceeding with refreshed stat info."
            else
                log ERROR "git update-index --refresh failed (exit $rc); index may be corrupt. Aborting before reset."
                return 1
            fi
        fi
    fi
    tmp="$(mktemp)" || { log ERROR "Failed to allocate temp file for diff-index"; return 1; }
    if "${GIT_CMD[@]}" diff-index --raw --no-renames -z HEAD -- >"$tmp" 2>/dev/null; then
        rc=0
    else
        rc=$?
        rm -f -- "$tmp" 2>/dev/null || true
        log ERROR "Failed to enumerate tracked changes (git diff-index exit $rc). Aborting before reset to avoid misreading failure as clean tree."
        return 1
    fi
    mapfile -d '' -t raw_records <"$tmp" || {
        rm -f -- "$tmp" 2>/dev/null || true
        log ERROR "Failed to parse tracked-changes output."
        return 1
    }
    rm -f -- "$tmp" 2>/dev/null || true

    count="${#raw_records[@]}"

    while (( i < count )); do
        meta="${raw_records[i]}"
        path="${raw_records[i+1]:-}"
        (( i += 2 ))

        [[ -n "$meta" ]] || continue

        # Reject malformed/incomplete records: must start with ':' and have 5 fields.
        case "$meta" in
            :*) ;;
            *) log ERROR "Malformed diff-index record (missing leading colon). Aborting."; return 1 ;;
        esac
        read -r oldmode newmode oldoid newoid status <<< "${meta#:}" || {
            log ERROR "Malformed diff-index record. Aborting."
            return 1
        }
        [[ "$oldmode" =~ ^[0-9]+$ && -n "$oldoid" && -n "$status" ]] || {
            log ERROR "Incomplete diff-index record. Aborting."
            return 1
        }
        status="${status%%[0-9]*}"

        [[ -n "$path" ]] || {
            log ERROR "Incomplete diff-index record (missing path). Aborting."
            return 1
        }

        CHANGE_PATHS+=("$path")
        CHANGE_STATUS["$path"]="$status"
        CHANGE_OLD_MODE["$path"]="$oldmode"
        CHANGE_OLD_OID["$path"]="$oldoid"
        CHANGE_BACKUP_HAS_FILE["$path"]=0

        (( parsed_count++ )) || true
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

    # Validate topology before mkdir: no symlink ancestor, no unexpected file.
    local cur="" comp="" r=""
    local -a comps=()
    if [[ "$parent_abs" != "$root" ]]; then
        r="${parent_abs#"$root"/}"
        IFS='/' read -r -a comps <<< "$r" || return 1
        cur="$root"
        for comp in "${comps[@]}"; do
            [[ -n "$comp" && "$comp" != "." && "$comp" != ".." ]] || return 1
            cur="${cur}/${comp}"
            if [[ -L "$cur" ]]; then
                return 1
            fi
            if [[ -e "$cur" && ! -d "$cur" ]]; then
                return 1
            fi
        done
    fi

    mkdir -p -- "$parent_abs" || return 1
    cache_ref["$parent_abs"]=1
    return 0
}

backup_user_modifications() {
    local backup_dir="" payload_root="" meta_root="" manifest_nul="" path="" status="" src="" dest=""
    local copied_count=0
    local required_bytes=0
    local path_bytes=0
    local -A mkdir_cache=()
    local -A payload_cache=()

    if [[ -n "$USER_MODS_BACKUP_DIR" && -d "$USER_MODS_BACKUP_DIR" ]]; then
        [[ "$USER_MODS_BACKUP_COMPLETE" == true ]] || {
            log ERROR "Previous user-mods backup is incomplete; preserving for review: $USER_MODS_BACKUP_DIR"
            return 1
        }
        return 0
    fi
    ((${#CHANGE_PATHS[@]} > 0)) || return 0

    for path in "${CHANGE_PATHS[@]}"; do
        status="${CHANGE_STATUS["$path"]:-?}"
        src="${WORK_TREE}/${path}"

        if [[ "$status" == "D" ]] || ! path_exists "$src"; then
            continue
        fi
        if has_symlink_ancestor "$src"; then
            log ERROR "Refusing to back up path with symlink ancestor (would leave worktree): $(quote_for_log "$path")"
            return 1
        fi

        path_bytes="$(path_copy_size_bytes "$src")"
        [[ "$path_bytes" =~ ^[0-9]+$ ]] || path_bytes=0
        ((required_bytes += path_bytes))
    done

    check_disk_space "$ACTIVE_BACKUP_BASE_DIR" || return 1
    ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$required_bytes" "modified-files backup" || return 1

    backup_dir="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "user_mods_${RUN_TIMESTAMP}_XXXXXX")" || {
        log ERROR "Failed to create modified-files backup directory"
        return 1
    }
    # Register partial early; completion flagged only at end.
    USER_MODS_BACKUP_DIR="$backup_dir"
    USER_MODS_BACKUP_COMPLETE=false
    payload_root="${backup_dir}/payload"
    meta_root="${backup_dir}/meta"
    if ! mkdir -p -- "$payload_root" "$meta_root"; then
        log ERROR "Failed to create user-mods payload/meta subdirectories"
        return 1
    fi
    chmod 700 -- "$payload_root" "$meta_root" 2>/dev/null || true
    manifest_nul="${meta_root}/MANIFEST.nul"
    if ! : > "$manifest_nul"; then
        log ERROR "Failed to create backup manifest"
        return 1
    fi
    chmod 600 -- "$manifest_nul" 2>/dev/null || true

    for path in "${CHANGE_PATHS[@]}"; do
        status="${CHANGE_STATUS["$path"]:-?}"
        src="${WORK_TREE}/${path}"

        if [[ "$status" == "D" || ! -e "$src" && ! -L "$src" ]]; then
            CHANGE_BACKUP_HAS_FILE["$path"]=0
            printf '%s\0%s\0%s\0%s\0' \
                "$status" "${CHANGE_OLD_OID["$path"]:-}" "0" "$path" >> "$manifest_nul" || {
                log ERROR "Failed to write backup manifest for $(quote_for_log "$path")"
                return 1
            }
            continue
        fi

        if has_symlink_ancestor "$src"; then
            log ERROR "Refusing to back up path with symlink ancestor: $(quote_for_log "$path")"
            return 1
        fi
        dest="${payload_root}/${path}"
        ensure_relative_parent_dir "$payload_root" "$path" payload_cache || {
            log ERROR "Failed to create backup parent directory for $(quote_for_log "$path")"
            return 1
        }

        if ! cp -aT --reflink=auto -- "$src" "$dest"; then
            log ERROR "Failed to back up modified file: $(quote_for_log "$path")"
            return 1
        fi

        CHANGE_BACKUP_HAS_FILE["$path"]=1
        printf '%s\0%s\0%s\0%s\0' \
            "$status" "${CHANGE_OLD_OID["$path"]:-}" "1" "$path" >> "$manifest_nul" || {
            log ERROR "Failed to write backup manifest for $(quote_for_log "$path")"
            return 1
        }
        ((copied_count++)) || true
    done

    USER_MODS_BACKUP_COMPLETE=true
    log OK "Backed up ${#CHANGE_PATHS[@]} tracked change(s) to: $backup_dir (payload/, meta/MANIFEST.nul)"
    if ((copied_count == 0)); then
        log INFO "Tracked changes were deletion-only; backup manifest preserved deletion intent"
    fi

    return 0
}

backup_full_tracked_tree() {
    local backup_dir="" payload_root="" meta_root="" info_file="" path="" src="" dest=""
    local copied_count=0
    local required_bytes=0
    local path_bytes=0
    local -A mkdir_cache=()
    local -a tracked_paths=()
    local tmp="" rc=0

    if [[ -n "$FULL_TRACKED_BACKUP_DIR" && -d "$FULL_TRACKED_BACKUP_DIR" ]]; then
        [[ "$FULL_TRACKED_BACKUP_COMPLETE" == true ]] || {
            log ERROR "Previous full tracked backup is incomplete; preserving: $FULL_TRACKED_BACKUP_DIR"
            return 1
        }
        return 0
    fi

    tmp="$(mktemp)" || { log ERROR "Failed to allocate temp file for ls-files"; return 1; }
    if "${GIT_CMD[@]}" ls-files -z >"$tmp" 2>/dev/null; then
        rc=0
    else
        rc=$?
        rm -f -- "$tmp" 2>/dev/null || true
        log ERROR "Failed to enumerate tracked files (git ls-files exit $rc). Aborting before destructive action."
        return 1
    fi
    mapfile -d '' -t tracked_paths <"$tmp" || {
        rm -f -- "$tmp" 2>/dev/null || true
        log ERROR "Failed to parse tracked file list."
        return 1
    }
    rm -f -- "$tmp" 2>/dev/null || true

    for path in "${tracked_paths[@]}"; do
        [[ -n "$path" ]] || {
            log ERROR "Malformed tracked path record (empty). Aborting."
            return 1
        }
        src="${WORK_TREE}/${path}"
        path_exists "$src" || continue
        if has_symlink_ancestor "$src"; then
            log ERROR "Refusing to back up path with symlink ancestor: $(quote_for_log "$path")"
            return 1
        fi
        path_bytes="$(path_copy_size_bytes "$src")"
        [[ "$path_bytes" =~ ^[0-9]+$ ]] || path_bytes=0
        ((required_bytes += path_bytes))
    done

    check_disk_space "$ACTIVE_BACKUP_BASE_DIR" || return 1
    ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$required_bytes" "full tracked-tree backup" || return 1

    backup_dir="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "pre_reset_${RUN_TIMESTAMP}_XXXXXX")" || {
        log ERROR "Failed to create full tracked backup directory"
        return 1
    }
    FULL_TRACKED_BACKUP_DIR="$backup_dir"
    FULL_TRACKED_BACKUP_COMPLETE=false
    payload_root="${backup_dir}/payload"
    meta_root="${backup_dir}/meta"
    if ! mkdir -p -- "$payload_root" "$meta_root"; then
        log ERROR "Failed to create full-tracked payload/meta subdirectories"
        return 1
    fi
    chmod 700 -- "$payload_root" "$meta_root" 2>/dev/null || true
    info_file="${meta_root}/INFO.txt"
    if ! : > "$info_file"; then
        log ERROR "Failed to create tracked-backup info file"
        return 1
    fi
    chmod 600 -- "$info_file" 2>/dev/null || true

    {
        printf 'Dusky full tracked-tree backup\n'
        printf 'Created: %s\n' "$RUN_TIMESTAMP"
        printf 'Repository HEAD before destructive action: %s\n' "$("${GIT_CMD[@]}" rev-parse HEAD 2>/dev/null || printf 'unknown')"
    } >> "$info_file" || {
        log ERROR "Failed to write tracked-backup info"
        return 1
    }

    for path in "${tracked_paths[@]}"; do
        src="${WORK_TREE}/${path}"
        if ! path_exists "$src"; then
            continue
        fi

        dest="${payload_root}/${path}"
        ensure_relative_parent_dir "$payload_root" "$path" mkdir_cache || {
            log ERROR "Failed to create tracked-backup directory for $(quote_for_log "$path")"
            return 1
        }

        if ! cp -aT --reflink=auto -- "$src" "$dest"; then
            log ERROR "Failed to back up tracked file: $(quote_for_log "$path")"
            return 1
        fi
        ((copied_count++)) || true
    done

    FULL_TRACKED_BACKUP_COMPLETE=true
    log OK "Full tracked-tree backup preserved at: $backup_dir/payload ($copied_count file(s)), meta/INFO.txt"
    return 0
}

backup_git_history() {
    local backup_root="" backup_repo="" meta_root="" info_file=""
    local required_bytes=0

    if [[ -n "$GIT_HISTORY_BACKUP_DIR" && -d "$GIT_HISTORY_BACKUP_DIR" ]]; then
        [[ "$GIT_HISTORY_BACKUP_COMPLETE" == true ]] || {
            log ERROR "Previous Git history backup is incomplete; preserving: $GIT_HISTORY_BACKUP_DIR"
            return 1
        }
        return 0
    fi

    required_bytes="$(path_copy_size_bytes "$DOTFILES_GIT_DIR")"
    [[ "$required_bytes" =~ ^[0-9]+$ ]] || required_bytes=0
    check_disk_space "$ACTIVE_BACKUP_BASE_DIR" || return 1
    ensure_free_space_for_bytes "$ACTIVE_BACKUP_BASE_DIR" "$required_bytes" "Git history backup" || return 1

    backup_root="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "repo_history_${RUN_TIMESTAMP}_XXXXXX")" || {
        log ERROR "Failed to create Git history backup directory"
        return 1
    }

    GIT_HISTORY_BACKUP_DIR="$backup_root"
    GIT_HISTORY_BACKUP_COMPLETE=false
    meta_root="${backup_root}/meta"
    if ! mkdir -p -- "$meta_root"; then
        log ERROR "Failed to create history meta subdirectory"
        return 1
    fi
    chmod 700 -- "$meta_root" 2>/dev/null || true
    backup_repo="${backup_root}/payload.git"
    if ! cp -aT --reflink=auto -- "$DOTFILES_GIT_DIR" "$backup_repo"; then
        log ERROR "Failed to preserve Git history backup"
        return 1
    fi

    info_file="${meta_root}/INFO.txt"
    {
        printf 'Dusky Git history backup\n'
        printf 'Created: %s\n' "$RUN_TIMESTAMP"
        printf 'Source: %s\n' "$DOTFILES_GIT_DIR"
        printf 'HEAD: %s\n' "$("${GIT_CMD[@]}" rev-parse HEAD 2>/dev/null || printf 'unknown')"
    } > "$info_file" || {
        log ERROR "Failed to write history info file"
        return 1
    }
    chmod 600 -- "$info_file" 2>/dev/null || true

    GIT_HISTORY_BACKUP_COMPLETE=true
    log OK "Git history backup preserved at: $backup_root (payload.git, meta/INFO.txt)"
    return 0
}

ensure_merge_dir() {
    local files_root="" markers_root="" meta_root=""

    if [[ -n "$MERGE_DIR" && -d "$MERGE_DIR" ]]; then
        return 0
    fi

    MERGE_DIR="$(make_private_dir_under "$ACTIVE_BACKUP_BASE_DIR" "needs_merge_${RUN_TIMESTAMP}_XXXXXX")" || {
        log ERROR "Failed to create merge directory"
        return 1
    }
    files_root="${MERGE_DIR}/files"
    markers_root="${MERGE_DIR}/deletion-markers"
    meta_root="${MERGE_DIR}/meta"
    mkdir -p -- "$files_root" "$markers_root" "$meta_root" || {
        log ERROR "Failed to create merge payload subdirectories"
        return 1
    }
    chmod 700 -- "$files_root" "$markers_root" "$meta_root" 2>/dev/null || true
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
    local head_rc=0
    local anc="" anc_meta="" anc_mode="" anc_oid="" anc_rc=0

    head_meta="" head_rc=0
    head_meta="$(git_head_path_meta "$path")" || head_rc=$?
    if ((head_rc != 0 && head_rc != 10)); then
        # Metadata execution failure (not legitimate missing): abort, preserve recovery.
        return 1
    fi
    if ((head_rc == 10)); then
        head_meta=""
        new_mode=""
        new_oid=""
    elif [[ -n "$head_meta" ]]; then
        IFS=$'\t' read -r new_mode new_oid <<< "$head_meta" || {
            return 1
        }
    fi

    if [[ -n "$old_oid" ]] && ! is_zero_oid "$old_oid"; then
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

    # Old staged addition below an upstream-created symlink/file is a conflict:
    # never follow that parent or overwrite unrelated data.
    anc="$path"
    while [[ "$anc" == */* ]]; do
        anc="${anc%/*}"
        anc_meta="" anc_rc=0
        anc_meta="$(git_head_path_meta "$anc")" || anc_rc=$?
        if ((anc_rc != 0 && anc_rc != 10)); then
            return 1
        fi
        if ((anc_rc == 10)); then
            anc_meta=""
        elif [[ -n "$anc_meta" ]]; then
            IFS=$'\t' read -r anc_mode anc_oid <<< "$anc_meta" || {
                return 1
            }
            # Tree mode is 040000; anything else (blob/symlink/commit) blocks descent.
            if [[ "$anc_mode" != "040000" ]]; then
                printf 'merge\t%s\t%s' "$new_mode" "$new_oid"
                return 0
            fi
        fi
    done

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
    return 0
}

atomic_restore_path() {
    local src="$1"
    local target="$2"
    local parent="" base="" probe_path="" tmpdir="" tmp="" displaced=""
    local copy_bytes=0
    local mv_rc=0
    local src_is_dir=false target_exists=false target_is_dir=false

    parent="$(path_parent "$target")"
    base="$(path_base "$target")"

    # Validate restore parent topology before mkdir/copy/move.
    if ! validate_restore_parent_topology "$target"; then
        log ERROR "Refusing restore with unsafe parent topology: $(quote_for_log "$target")"
        return 1
    fi
    if ! mkdir -p -- "$parent"; then
        log ERROR "Failed to create restore parent for $(quote_for_log "$target")"
        return 1
    fi
    # Re-validate after mkdir (mkdir must not have traversed symlinks).
    if ! validate_restore_parent_topology "$target"; then
        log ERROR "Restore parent became unsafe after mkdir: $(quote_for_log "$target")"
        return 1
    fi

    [[ -d "$src" && ! -L "$src" ]] && src_is_dir=true || src_is_dir=false
    if path_exists "$target"; then
        target_exists=true
        [[ -d "$target" && ! -L "$target" ]] && target_is_dir=true || target_is_dir=false
    else
        target_exists=false
        target_is_dir=false
    fi

    copy_bytes="$(path_copy_size_bytes "$src")"
    [[ "$copy_bytes" =~ ^[0-9]+$ ]] || copy_bytes=0
    if (( copy_bytes > 0 )); then
        probe_path="$(nearest_existing_ancestor "$parent")"
        ensure_free_space_for_bytes "$probe_path" "$copy_bytes" "restoring $(quote_for_log "$target")" || return 1
    fi

    tmpdir="$(mktemp -d -p "$parent" ".${base}.dusky_tmp.XXXXXX")" || return 1
    CREATED_TEMP_DIRS+=("$tmpdir")
    CREATED_TEMP_STATUS["$tmpdir"]="active"

    tmp="${tmpdir}/${base}"
    displaced="${tmpdir}/.old_${base}"

    if ! cp -aT --reflink=auto -- "$src" "$tmp"; then
        CREATED_TEMP_STATUS["$tmpdir"]="failed-copy"
        return 1
    fi

    # Single atomic replacement for supported file/symlink cases (no missing window).
    if [[ "$src_is_dir" == false && ("$target_exists" == false || "$target_is_dir" == false) ]]; then
        if mv -fT -- "$tmp" "$target"; then
            CREATED_TEMP_STATUS["$tmpdir"]="completed-atomic"
            TEMP_DISPLACED_FOR_TMPDIR["$tmpdir"]=""
            rm -rf -- "$tmpdir" 2>/dev/null || true
            return 0
        else
            mv_rc=$?
            CREATED_TEMP_STATUS["$tmpdir"]="failed-atomic"
            # Target unchanged (atomic rename failed); tmp holds copy, preserve for diagnosis.
            log ERROR "Atomic restore failed for $(quote_for_log "$target") (mv exit $mv_rc); original preserved, temp retained at $tmpdir"
            return "$mv_rc"
        fi
    fi

    # Directory transitions require multiple steps: journal and preserve displaced data.
    if [[ "$target_exists" == true ]]; then
        if mv -fT -- "$target" "$displaced"; then
            TEMP_DISPLACED_FOR_TMPDIR["$tmpdir"]="$displaced"
            CREATED_TEMP_STATUS["$tmpdir"]="displaced"
        else
            mv_rc=$?
            CREATED_TEMP_STATUS["$tmpdir"]="failed-displace"
            log ERROR "Failed to displace target for directory restore: $(quote_for_log "$target") (mv exit $mv_rc)"
            return "$mv_rc"
        fi
    fi

    if mv -fT -- "$tmp" "$target"; then
        # Success: displaced original remains in tmpdir for safety until cleanup
        # explicitly verifies target; do not delete displaced yet here.
        CREATED_TEMP_STATUS["$tmpdir"]="completed-journaled"
        return 0
    else
        mv_rc=$?
        CREATED_TEMP_STATUS["$tmpdir"]="failed-restore"
        # Preserve displaced data on failure/interruption; attempt rollback where safe.
        if [[ -n "${TEMP_DISPLACED_FOR_TMPDIR["$tmpdir"]:-}" ]] && path_exists "$displaced" && ! path_exists "$target"; then
            if mv -fT -- "$displaced" "$target" 2>/dev/null; then
                CREATED_TEMP_STATUS["$tmpdir"]="rolled-back"
                TEMP_DISPLACED_FOR_TMPDIR["$tmpdir"]=""
            else
                log ERROR "Rollback failed for $(quote_for_log "$target"); displaced original retained at $displaced"
            fi
        fi
        return "$mv_rc"
    fi
}

restore_user_modifications() {
    local path="" status="" old_mode="" old_oid="" backup_src="" target="" merge_dest="" marker=""
    local plan="" action="" new_mode="" new_oid="" plan_rc=0
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
    local -A files_cache=()
    local -A markers_cache=()
    local -A restore_device_probe=()
    local -A restore_device_peak=()
    local -A restore_device_delta=()
    local -A planned_action=()
    local -A planned_new_mode=()
    local -A planned_new_oid=()
    local payload_root=""

    if [[ -z "$USER_MODS_BACKUP_DIR" || ! -d "$USER_MODS_BACKUP_DIR" ]]; then
        return 0
    fi
    payload_root="${USER_MODS_BACKUP_DIR}/payload"
    [[ -d "$payload_root" ]] || {
        log ERROR "User-mods backup payload missing: $payload_root. Preserving recovery material."
        return 1
    }

    (( ${#CHANGE_PATHS[@]} > 0 )) || return 0

    for path in "${CHANGE_PATHS[@]}"; do
        status="${CHANGE_STATUS["$path"]:-?}"
        old_mode="${CHANGE_OLD_MODE["$path"]:-}"
        old_oid="${CHANGE_OLD_OID["$path"]:-}"
        backup_src="${payload_root}/${path}"
        target="${WORK_TREE}/${path}"

        if [[ "$status" != "D" ]]; then
            if [[ "${CHANGE_BACKUP_HAS_FILE["$path"]:-0}" != "1" ]]; then
                log ERROR "Missing expected backup payload for $(quote_for_log "$path") (manifest says no copy). Preserving recovery material at $USER_MODS_BACKUP_DIR."
                return 1
            fi
            if [[ ! -e "$backup_src" && ! -L "$backup_src" ]]; then
                log ERROR "Missing expected backup payload for $(quote_for_log "$path"): $backup_src. Preserving recovery material."
                return 1
            fi
        fi

        plan="" plan_rc=0
        plan="" plan_rc=0
        if plan="$(classify_restore_action "$path" "$status" "$old_mode" "$old_oid")"; then
            plan_rc=0
        else
            plan_rc=$?
            log ERROR "Failed to classify restore action for $(quote_for_log "$path") (metadata failure exit $plan_rc). Preserving recovery material."
            return 1
        fi
        IFS=$'\t' read -r action new_mode new_oid <<< "$plan" || {
            log ERROR "Malformed restore plan for $(quote_for_log "$path"). Preserving recovery material."
            return 1
        }

        planned_action["$path"]="$action"
        planned_new_mode["$path"]="$new_mode"
        planned_new_oid["$path"]="$new_oid"

        case "$action" in
            restore)
                backup_bytes="$(path_copy_size_bytes "$backup_src")"
                [[ "$backup_bytes" =~ ^[0-9]+$ ]] || backup_bytes=0
                target_bytes=0
                if path_exists "$target"; then
                    target_bytes="$(path_copy_size_bytes "$target")"
                    [[ "$target_bytes" =~ ^[0-9]+$ ]] || target_bytes=0
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
                [[ "$backup_bytes" =~ ^[0-9]+$ ]] || backup_bytes=0
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
        backup_src="${payload_root}/${path}"
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
                # Never remove unexpected live directories as a supposed deletion.
                if [[ -d "$target" && ! -L "$target" ]]; then
                    log WARN "Tracked deletion target is now a directory, queuing manual merge instead of deleting: $(quote_for_log "$path")"
                    ensure_merge_dir || { all_ok=false; continue; }
                    marker="${MERGE_DIR}/deletion-markers/${path}"
                    ensure_relative_parent_dir "${MERGE_DIR}/deletion-markers" "$path" markers_cache || {
                        log ERROR "Failed to create deletion-marker directory for: $(quote_for_log "$path")"
                        all_ok=false
                        continue
                    }
                    {
                        printf 'Tracked deletion requires manual review (target is directory).\n'
                        printf 'Path: %s\n' "$path"
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
                    continue
                fi
                if ! validate_restore_parent_topology "$target"; then
                    log ERROR "Refusing deletion with unsafe parent topology: $(quote_for_log "$path")"
                    all_ok=false
                    continue
                fi
                if ! rm -f -- "$target"; then
                    log ERROR "Failed to re-apply tracked deletion for: $(quote_for_log "$path")"
                    all_ok=false
                    continue
                fi
                # Ensure we did not delete a directory: rm -f never deletes dirs, safe.
                (( deletion_count++ )) || true
                ;;
            delete-merge)
                ensure_merge_dir || {
                    all_ok=false
                    continue
                }

                marker="${MERGE_DIR}/deletion-markers/${path}"
                ensure_relative_parent_dir "${MERGE_DIR}/deletion-markers" "$path" markers_cache || {
                    log ERROR "Failed to create deletion-marker directory for: $(quote_for_log "$path")"
                    all_ok=false
                    continue
                }

                {
                    printf 'Tracked deletion requires manual review.\n'
                    printf 'Path: %s\n' "$path"
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
                log RAW "  → Manual review needed for tracked deletion: $(quote_for_log "$path") (marker: $marker)"
                ;;
            restore)
                if ! validate_restore_parent_topology "$target"; then
                    log ERROR "Refusing restore with unsafe parent topology: $(quote_for_log "$path")"
                    all_ok=false
                    continue
                fi
                if atomic_restore_path "$backup_src" "$target"; then
                    (( restored_count++ )) || true
                    log RAW "  → Restored: $(quote_for_log "$path")"
                else
                    log ERROR "Failed to restore and rolled back where safe: $(quote_for_log "$path") (backup retained at $USER_MODS_BACKUP_DIR)"
                    all_ok=false
                fi
                ;;
            merge)
                ensure_merge_dir || {
                    all_ok=false
                    continue
                }

                merge_dest="${MERGE_DIR}/files/${path}"
                ensure_relative_parent_dir "${MERGE_DIR}/files" "$path" files_cache || {
                    log ERROR "Failed to create merge directory for: $(quote_for_log "$path")"
                    all_ok=false
                    continue
                }

                if [[ -e "$merge_dest" || -L "$merge_dest" ]]; then
                    log ERROR "Merge destination already exists, retaining both versions: $(quote_for_log "$path")"
                    all_ok=false
                    continue
                fi
                if ! cp -aT --reflink=auto -- "$backup_src" "$merge_dest"; then
                    log ERROR "Failed to save merge copy for: $(quote_for_log "$path")"
                    all_ok=false
                    continue
                fi

                (( merge_count++ )) || true
                log RAW "  → Upstream changed: $(quote_for_log "$path") (your version saved for merge: $merge_dest)"
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
        log WARN "MANUAL MERGE REQUIRED — review saved files in: ${MERGE_DIR}/files and markers in: ${MERGE_DIR}/deletion-markers (merge root: $MERGE_DIR)"
    fi
    if (( deletion_count > 0 )); then
        log WARN "$deletion_count tracked deletion(s) preserved or queued for manual merge"
    fi
    if (( restored_count == 0 && merge_count == 0 && deletion_count == 0 )); then
        log INFO "No modifications needed restoring."
    fi

    if [[ "$all_ok" == true ]]; then
        if ((merge_count > 0)); then
            # Preserve recovery material when manual work remains.
            log WARN "Restore completed with manual-merge work outstanding; user-mods backup preserved at: $USER_MODS_BACKUP_DIR"
            return 0
        fi
        # Only delete backup when no manual work remains and all restores succeeded.
        rm -rf -- "$USER_MODS_BACKUP_DIR" 2>/dev/null || true
        USER_MODS_BACKUP_DIR=""
        USER_MODS_BACKUP_COMPLETE=false
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
        if ! read -r -t "$PROMPT_TIMEOUT_LONG" -p "Clone from ${REPO_URL}? [y/N] " do_clone; then
            do_clone="n"
        fi
        do_clone="${do_clone:-n}"
    elif [[ "$OPT_FORCE" == true ]]; then
        do_clone="y"
    fi

    if [[ ! "$do_clone" =~ ^[Yy]$ ]]; then
        log INFO "Clone cancelled."
        return "$SYNC_RC_RECOVERABLE"
    fi

    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "[DRY-RUN] Would clone branch ${BRANCH}: $REPO_URL → $DOTFILES_GIT_DIR (preview only, no changes)"
        log INFO "[DRY-RUN] Post-sync script validation is unavailable until sync completes."
        return 0
    fi

    log INFO "Cloning bare repository (private temp sibling, atomic publish)..."
    if ! clone_with_retry; then
        log ERROR "Clone failed; configured repo path was never overwritten."
        return "$SYNC_RC_UNSAFE"
    fi

    ensure_repo_defaults || {
        log ERROR "Failed to set repo defaults after clone."
        return "$SYNC_RC_UNSAFE"
    }

    log INFO "Checking out files..."
    # Mark incomplete so a failed checkout cannot be misread as success next run.
    : > "${DOTFILES_GIT_DIR}/.dusky_checkout_incomplete" 2>/dev/null || {
        log ERROR "Failed to create checkout-incomplete marker."
        return "$SYNC_RC_UNSAFE"
    }
    if ! backup_worktree_collisions_for_ref "HEAD" false; then
        log ERROR "Collision backup failed before checkout."
        return "$SYNC_RC_UNSAFE"
    fi

    if ! "${GIT_CMD[@]}" checkout >> "$LOG_FILE" 2>&1; then
        log ERROR "Checkout failed. Repository left marked incomplete; collision backups preserved. Resolve manually."
        # Attempt rollback of collision moves where safe, but keep marker.
        rollback_collision_moves || true
        return "$SYNC_RC_UNSAFE"
    fi

    rm -f -- "${DOTFILES_GIT_DIR}/.dusky_checkout_incomplete" 2>/dev/null || {
        log ERROR "Checkout succeeded but failed to clear incomplete marker."
        return "$SYNC_RC_UNSAFE"
    }

    log OK "Repository cloned and checked out successfully."
    return 0
}

initialize_unborn_repo_from_ref() {
    local remote_oid="${1:?missing remote OID}"

    if ! is_hex_oid "$remote_oid"; then
        log ERROR "Invalid pinned remote OID for unborn initialization: $remote_oid"
        return 1
    fi

    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "[DRY-RUN] Would initialize unborn repository from $remote_oid (preview only)"
        return 0
    fi

    # Pre-mutation snapshot: preserve staged-only blobs/files before any mutation.
    check_index_supported_state || return 1
    backup_git_history || return 1
    backup_full_tracked_tree || return 1

    if ! "${GIT_CMD[@]}" symbolic-ref HEAD "refs/heads/${BRANCH}" >> "$LOG_FILE" 2>&1; then
        log ERROR "Failed to point HEAD at refs/heads/${BRANCH}"
        return 1
    fi

    if ! backup_worktree_collisions_for_ref "$remote_oid" false; then
        return 1
    fi

    if "${GIT_CMD[@]}" reset --hard "$remote_oid" >> "$LOG_FILE" 2>&1; then
        log OK "Initialized existing empty repository from upstream ($remote_oid). Staged pre-run state preserved in $GIT_HISTORY_BACKUP_DIR / $FULL_TRACKED_BACKUP_DIR."
        return 0
    fi

    log ERROR "Failed to initialize unborn repository from upstream ($remote_oid)."
    if ! rollback_collision_moves; then
        log ERROR "Collision rollback incomplete after unborn init failure."
    fi
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
    local local_rc=0 remote_rc=0
    local sync_choice="1" sync_dec=0
    local rebase_output="" rebase_rc=0 abort_rc=0 prep_rc=0
    local mb_rc=0
    local symref="" symref_rc=0
    local branch_oid="" branch_rc=0
    local _rb_ok=true _rs_ok=true

    get_repo_state
    repo_state="$REPLY"

    case "$repo_state" in
        absent)
            if initial_clone; then
                log OK "Repository synchronized (initial clone)."
                return 0
            else
                clone_rc=$?
                return "$clone_rc"
            fi
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
        log INFO "[DRY-RUN] Would fetch branch ${BRANCH} from ${fetch_source} (preview only, cached-ref status below)"
    else
        fetch_with_retry "$fetch_source" || return "$SYNC_RC_RECOVERABLE"
        log OK "Fetch complete."
    fi

    log INFO "Checking sync status (cached-ref preview in dry-run)..."
    local_head="" local_rc=0
    local_head="$("${GIT_CMD[@]}" rev-parse --verify HEAD 2>/dev/null)" || local_rc=$?
    remote_head="" remote_rc=0
    remote_head="$("${GIT_CMD[@]}" rev-parse --verify "$remote_ref" 2>/dev/null)" || remote_rc=$?

    if ((remote_rc != 0)) || [[ -z "$remote_head" ]] || ! is_hex_oid "$remote_head"; then
        if [[ "$OPT_DRY_RUN" == true ]]; then
            log WARN "[DRY-RUN] No cached upstream ref found ($remote_ref, git exit $remote_rc). Cannot preview sync status; post-sync scripts unavailable."
            return 0
        fi
        log ERROR "Cannot determine upstream HEAD for ${BRANCH} ($remote_ref, git exit $remote_rc). Repository may be corrupted; inspect manually."
        return "$SYNC_RC_UNSAFE"
    fi
    PINNED_REMOTE_OID="$remote_head"

    if ((local_rc != 0)) || [[ -z "$local_head" ]]; then
        # Distinguish truly unborn HEAD from corrupt/unresolvable HEAD.
        # Must not use `rev-list --all` (includes freshly fetched tracking ref).
        symref="" symref_rc=0
        symref="$("${GIT_CMD[@]}" symbolic-ref -q HEAD 2>/dev/null)" || symref_rc=$?
        if ((symref_rc == 0)) && [[ "$symref" == "refs/heads/${BRANCH}" ]]; then
            branch_oid=""; branch_rc=0
            branch_oid="$("${GIT_CMD[@]}" rev-parse --verify "refs/heads/${BRANCH}" 2>/dev/null)" || branch_rc=$?
            if ((branch_rc != 0)); then
                # Target branch has no commits locally → truly unborn, even though
                # fetched tracking ref exists. Corruption would be branch exists
                # but HEAD unresolvable.
                if [[ "$OPT_DRY_RUN" == true ]]; then
                    log INFO "[DRY-RUN] Existing repository has an unborn HEAD (refs/heads/${BRANCH} missing locally). Would initialize it from $remote_head (preview only)."
                    return 0
                fi
                log WARN "Local branch refs/heads/${BRANCH} is unborn (no local commits). Initializing it from upstream..."
                PRE_SYNC_HEAD=""
                PRE_SYNC_HEAD_OID=""
                initialize_unborn_repo_from_ref "$remote_head" || return "$SYNC_RC_UNSAFE"
                ensure_repo_defaults || return "$SYNC_RC_UNSAFE"
                log OK "Repository synchronized."
                return 0
            fi
        fi
        log ERROR "Local HEAD is unresolvable but repository is not cleanly unborn (rev-parse HEAD exit $local_rc, symbolic-ref exit $symref_rc, branch check rc ${branch_rc:-?}). Rejecting before mutation; inspect manually."
        return "$SYNC_RC_UNSAFE"
    fi
    if ! is_hex_oid "$local_head"; then
        log ERROR "Local HEAD is malformed ($local_head). Rejecting before mutation."
        return "$SYNC_RC_UNSAFE"
    fi
    PRE_SYNC_HEAD="$local_head"
    PRE_SYNC_HEAD_OID="$local_head"

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
            log WARN "HEAD matches ${remote_ref} ($remote_head), but ${unhealthy_tracked} tracked path(s) are missing or type-mismatched in the work tree."
            log INFO "Leaving local files untouched because those paths may be intentional user changes."
        else
            log OK "Already up to date ($local_head)."
        fi

        if [[ "$OPT_DRY_RUN" == true ]]; then
            log INFO "[DRY-RUN] Same-HEAD preview is read-only; index was not refreshed."
        else
            ensure_repo_defaults || return "$SYNC_RC_UNSAFE"
        fi
        return 0
    fi

    base_commit="" mb_rc=0
    base_commit="$("${GIT_CMD[@]}" merge-base "$local_head" "$remote_head" 2>/dev/null)" || mb_rc=$?
    if (( mb_rc == 1 )) || { ((mb_rc == 0)) && [[ -z "$base_commit" ]]; }; then
        if handle_unrelated_upstream_history "$remote_head"; then
            if [[ "$OPT_DRY_RUN" == true ]]; then
                log INFO "[DRY-RUN] Unrelated-history preview only."
            else
                ensure_repo_defaults || return "$SYNC_RC_UNSAFE"
                log OK "Repository synchronized."
            fi
            return 0
        else
            return $?
        fi
    elif (( mb_rc != 0 )); then
        log ERROR "Cannot determine merge-base with upstream (git exit code $mb_rc). Repository may be corrupted."
        return "$SYNC_RC_UNSAFE"
    fi

    # Distinguish local-ahead from truly diverged.
    if [[ "$base_commit" == "$remote_head" ]]; then
        log OK "Local is ahead of upstream ($local_head ahead of $remote_head); preserving local commits, no upstream commits to apply."
        show_update_preview "$local_head" "$remote_head" "$base_commit" || true
        if [[ "$OPT_DRY_RUN" != true ]]; then
            ensure_repo_defaults || return "$SYNC_RC_UNSAFE"
        fi
        return 0
    fi

    show_update_preview "$local_head" "$remote_head" "$base_commit"

    if [[ "$base_commit" == "$local_head" ]]; then
        log INFO "Fast-forwarding to upstream ($remote_head)..."

        if [[ "$OPT_DRY_RUN" == true ]]; then
            log INFO "[DRY-RUN] Would back up index/worktree state and reset --hard to $remote_head (preview only, no changes)"
            return 0
        fi

        # Snapshot HEAD/index/worktree BEFORE any collision moves; use snapshot afterward.
        check_index_supported_state || return "$SYNC_RC_UNSAFE"
        backup_git_history || return "$SYNC_RC_UNSAFE"
        capture_tracked_changes_manifest || return "$SYNC_RC_UNSAFE"
        backup_full_tracked_tree || return "$SYNC_RC_UNSAFE"
        backup_user_modifications || {
            log ERROR "Backup failed. Aborting update to protect your files (no collision moves yet)."
            return "$SYNC_RC_UNSAFE"
        }
        if ! backup_worktree_collisions_for_ref "$remote_head" true; then
            log ERROR "Collision backup failed; aborting before reset."
            return "$SYNC_RC_UNSAFE"
        fi

        if "${GIT_CMD[@]}" reset --hard "$remote_head" >> "$LOG_FILE" 2>&1; then
            log OK "Updated to latest ($remote_head)."
            restore_user_modifications || return "$SYNC_RC_UNSAFE"
            ensure_repo_defaults || return "$SYNC_RC_UNSAFE"
        else
            log ERROR "Reset to $remote_head failed. Collision/user backups preserved; original HEAD was $PRE_SYNC_HEAD_OID."
            if ! rollback_collision_moves; then
                log ERROR "Collision rollback incomplete after reset failure; recovery material preserved."
            fi
            return "$SYNC_RC_UNSAFE"
        fi
    else
        log WARN "Local history diverged from upstream (base $base_commit, local $local_head, remote $remote_head)."

        if [[ "$OPT_DRY_RUN" == true ]]; then
            log INFO "[DRY-RUN] History diverged. Would require reset or rebase to $remote_head (preview only, no changes)."
            return 0
        fi

        if [[ -t 0 && "$OPT_FORCE" != true ]]; then
            printf '\n%s[DIVERGED HISTORY]%s Choose sync method:\n' "$CLR_YLW" "$CLR_RST"
            printf '  1) Abort (keep current state) [DEFAULT]\n'
            printf '  %s2) Reset to upstream [RECOMMENDED]%s\n' "$CLR_GRN" "$CLR_RST"
            printf '     Your uncommitted tweaks will be backed up and auto-restored where safe.\n'
            printf '     Local commits will be preserved in Git history backup; reset discards them from the worktree.\n'
            printf '  3) Attempt rebase (may fail; never auto-resets on failure)\n\n'
            if ! read -r -t "$PROMPT_TIMEOUT_LONG" -p "Choice [1-3] (default: 1): " sync_choice; then
                sync_choice="1"
            fi
            sync_choice="${sync_choice:-1}"
            parse_decimal_choice "$sync_choice" 1 3 sync_dec || sync_dec=1
            sync_choice="$sync_dec"
        elif [[ "$OPT_ALLOW_DIVERGED_RESET" == true ]]; then
            sync_choice="2"
        else
            log ERROR "Unattended/force mode and diverged history. Aborting to prevent data loss (use --allow-diverged-reset to override)."
            return "$SYNC_RC_RECOVERABLE"
        fi

        case "$sync_choice" in
            1)
                log INFO "Aborted by user."
                return "$SYNC_RC_RECOVERABLE"
                ;;
            2)
                check_index_supported_state || return "$SYNC_RC_UNSAFE"
                backup_git_history || return "$SYNC_RC_UNSAFE"
                capture_tracked_changes_manifest || return "$SYNC_RC_UNSAFE"
                backup_full_tracked_tree || return "$SYNC_RC_UNSAFE"
                backup_user_modifications || return "$SYNC_RC_UNSAFE"
                if ! backup_worktree_collisions_for_ref "$remote_head" true; then
                    log ERROR "Collision backup failed; aborting before reset."
                    return "$SYNC_RC_UNSAFE"
                fi

                log INFO "Resetting to upstream ($remote_head)..."
                if "${GIT_CMD[@]}" reset --hard "$remote_head" >> "$LOG_FILE" 2>&1; then
                    log OK "Reset complete ($remote_head). Original HEAD $PRE_SYNC_HEAD_OID preserved in $GIT_HISTORY_BACKUP_DIR."
                    restore_user_modifications || return "$SYNC_RC_UNSAFE"
                    ensure_repo_defaults || return "$SYNC_RC_UNSAFE"
                else
                    log ERROR "Reset to $remote_head failed. Original HEAD $PRE_SYNC_HEAD_OID preserved in $GIT_HISTORY_BACKUP_DIR."
                    if ! rollback_collision_moves; then
                        log ERROR "Collision rollback incomplete after reset failure."
                    fi
                    return "$SYNC_RC_UNSAFE"
                fi
                ;;
            3)
                check_index_supported_state || return "$SYNC_RC_UNSAFE"
                backup_git_history || return "$SYNC_RC_UNSAFE"
                capture_tracked_changes_manifest || return "$SYNC_RC_UNSAFE"
                backup_full_tracked_tree || return "$SYNC_RC_UNSAFE"
                backup_user_modifications || return "$SYNC_RC_UNSAFE"
                if ! backup_worktree_collisions_for_ref "$remote_head" true; then
                    log ERROR "Collision backup failed; aborting before rebase."
                    return "$SYNC_RC_UNSAFE"
                fi

                if "${GIT_CMD[@]}" reset --hard HEAD >> "$LOG_FILE" 2>&1; then
                    prep_rc=0
                else
                    prep_rc=$?
                    log ERROR "Preparation reset --hard HEAD failed (exit $prep_rc). Aborting rebase; original HEAD $PRE_SYNC_HEAD_OID preserved in $GIT_HISTORY_BACKUP_DIR."
                    if ! rollback_collision_moves; then
                        log ERROR "Collision rollback incomplete after prep-reset failure."
                    fi
                    return "$SYNC_RC_UNSAFE"
                fi
                log INFO "Attempting rebase onto $remote_head (no fallback reset authorized)..."
                rebase_output="" rebase_rc=0
                if rebase_output="$("${GIT_CMD[@]}" rebase "$remote_head" 2>&1)"; then
                    rebase_rc=0
                else
                    rebase_rc=$?
                fi
                printf '%s\n' "$rebase_output" >> "$LOG_FILE"

                if (( rebase_rc != 0 )); then
                    log ERROR "Rebase onto $remote_head failed (exit $rebase_rc). Aborting rebase and restoring pre-run state; never silently discarding local commits."
                    if "${GIT_CMD[@]}" rebase --abort >> "$LOG_FILE" 2>&1; then
                        abort_rc=0
                    else
                        abort_rc=$?
                        log ERROR "Rebase --abort failed (exit $abort_rc). Original HEAD $PRE_SYNC_HEAD_OID preserved in $GIT_HISTORY_BACKUP_DIR; resolve manually. Worktree/collisions not restored due to abort failure."
                        return "$SYNC_RC_UNSAFE"
                    fi
                    # Restore original HEAD where safe (pinned pre-sync OID, not upstream).
                    if [[ -n "$PRE_SYNC_HEAD_OID" ]]; then
                        if "${GIT_CMD[@]}" reset --hard "$PRE_SYNC_HEAD_OID" >> "$LOG_FILE" 2>&1; then
                            log INFO "Restored original HEAD $PRE_SYNC_HEAD_OID after failed rebase."
                        else
                            log ERROR "Failed to restore original HEAD $PRE_SYNC_HEAD_OID after failed rebase. History backup at $GIT_HISTORY_BACKUP_DIR; worktree/collisions not restored."
                            return "$SYNC_RC_UNSAFE"
                        fi
                    else
                        log ERROR "No pinned pre-sync HEAD to restore after failed rebase; history at $GIT_HISTORY_BACKUP_DIR."
                        return "$SYNC_RC_UNSAFE"
                    fi
                    # Restore pre-run worktree: collisions rollback + tracked modifications.
                    # Staged blobs remain in history backup (payload.git); worktree content restored.
                    # Do not claim full original state when only HEAD restored.
                    _rb_ok=true; _rs_ok=true
                    if ! rollback_collision_moves; then
                        log ERROR "Collision rollback incomplete after failed rebase; untracked pre-run state not fully restored."
                        _rb_ok=false
                    fi
                    if ! restore_user_modifications; then
                        log ERROR "Tracked worktree restore incomplete after failed rebase; recovery material preserved."
                        _rs_ok=false
                    fi
                    if [[ "$_rb_ok" != true || "$_rs_ok" != true ]]; then
                        log ERROR "Rebase failed; pre-run state only partially restored (HEAD $PRE_SYNC_HEAD_OID restored, worktree/collisions partial). No fallback reset performed."
                        return "$SYNC_RC_UNSAFE"
                    fi
                    log ERROR "Rebase failed; pre-run HEAD/worktree/collisions restored from backups. No fallback reset performed. Original commits preserved in $GIT_HISTORY_BACKUP_DIR."
                    return "$SYNC_RC_UNSAFE"
                else
                    log OK "Rebase onto $remote_head successful."
                    restore_user_modifications || return "$SYNC_RC_UNSAFE"
                    ensure_repo_defaults || return "$SYNC_RC_UNSAFE"
                fi
                ;;
            *)
                log INFO "Invalid choice. Aborting."
                return "$SYNC_RC_RECOVERABLE"
                ;;
        esac
    fi

    log OK "Repository synchronized ($remote_head)."
    return 0
}

# ==============================================================================
# SUDO MANAGEMENT
# ==============================================================================
init_sudo() {
    # Track initialized state independently of helper PID.
    if [[ "$SUDO_INITIALIZED" == true ]]; then
        if [[ -n "$SUDO_PID" ]] && kill -0 "$SUDO_PID" 2>/dev/null; then
            # Renew cached credentials too; sudo -n true alone proves cache, not policy.
            if sudo -n -v 2>/dev/null; then
                return 0
            fi
            log WARN "Sudo helper alive but credential renewal failed; re-acquiring."
        else
            # Helper died; validate auth before restarting.
            if sudo -n -v 2>/dev/null; then
                # Credentials still cached; restart keepalive.
                :
            else
                log WARN "Sudo keepalive helper not running; re-acquiring."
            fi
        fi
    fi

    # Validate sudo exists before any privileged use.
    command -v sudo >/dev/null 2>&1 || {
        log ERROR "sudo is required but not installed or not in PATH"
        return 1
    }

    if sudo -n -v 2>/dev/null; then
        log INFO "Cached sudo credentials present; starting renewal keepalive."
    else
        log INFO "Acquiring sudo privileges for execution sequence (cached credentials will be renewed)..."
        if ! sudo -v; then
            log ERROR "Sudo authentication failed. See sudo diagnostics above."
            return 1
        fi
    fi

    (
        trap 'exit 0' TERM

        while kill -0 "$MAIN_PID" 2>/dev/null; do
            sleep "$SUDO_KEEPALIVE_INTERVAL" &
            wait $! 2>/dev/null || true
            if ! sudo -n -v 2>/dev/null; then
                printf 'Sudo credential renewal failed; keepalive exiting\n' >&2
                exit 1
            fi
        done
    ) &
    SUDO_PID=$!
    SUDO_INITIALIZED=true
}

stop_sudo() {
    if [[ -n "$SUDO_PID" ]] && kill -0 "$SUDO_PID" 2>/dev/null; then
        kill "$SUDO_PID" 2>/dev/null || true
        wait "$SUDO_PID" 2>/dev/null || true
    fi
    SUDO_PID=""
    # Keep SUDO_INITIALIZED true to remember that auth was validated; helper
    # death is detected via PID liveness on next init_sudo.
}

# ==============================================================================
# SCRIPT EXECUTION ENGINE
# ==============================================================================
execute_scripts() {
    log SECTION "Executing Update Sequence"

    local i=0 total="${#MANIFEST_MODE[@]}"
    local mode="" script="" ignore_fail="" script_path=""
    local path_state="" quoted_args="" interpreter=""
    local -a args=()
    local -a interpreter_cmd=()

    for i in "${!MANIFEST_MODE[@]}"; do
        mode="${MANIFEST_MODE[$i]}"
        script="${MANIFEST_SCRIPT[$i]}"
        ignore_fail="${MANIFEST_IGNORE_FAIL[$i]}"
        script_path="${MANIFEST_PATH[$i]}"
        path_state="${MANIFEST_PATH_STATE[$i]}"
        interpreter="${MANIFEST_INTERPRETER[$i]}"
        local -n argv_ref="${MANIFEST_ARGV_NAME[$i]}"
        args=("${argv_ref[@]}")
        quoted_args=""
        if ((${#args[@]} > 0)); then
            quoted_args="$(join_quoted_argv "${args[@]}")"
        fi
        local -n interp_argv_ref="${MANIFEST_INTERPRETER_ARGV_NAME[$i]}"
        interpreter_cmd=("${interp_argv_ref[@]}")
        if ((${#interpreter_cmd[@]} == 0)); then
            # Fallback: should not happen after structural parsing; fail closed.
            log ERROR "Missing interpreter argv for $script; aborting entry."
            HARD_FAILED_SCRIPTS+=("$script (missing-interpreter)")
            FAILED_SCRIPT_DIRS["${script_path%/*}"]=1
            FAILED_COMMAND="$script"
            FAILED_EXIT=127
            if [[ "$OPT_STOP_ON_FAIL" == true ]]; then
                return 1
            fi
            # Default is stop-on-required-failure: do not silently continue.
            return 1
        fi

        case "$path_state" in
            ok)
                ;;
            *)
                # Conflicts or missing scripts were already caught and logged in Preflight
                HARD_FAILED_SCRIPTS+=("$script ($path_state)")
                FAILED_COMMAND="$script"
                FAILED_EXIT=127
                continue
                ;;
        esac

        if [[ "$mode" == "S" && "$OPT_DRY_RUN" != true ]]; then
            if ! init_sudo; then
                log ERROR "Sudo validation failed before privileged script: $script"
                HARD_FAILED_SCRIPTS+=("$script (sudo-unavailable)")
                FAILED_COMMAND="sudo $script"
                FAILED_EXIT=1
                return 1
            fi
            # Re-validate helper liveness/auth before each S entry.
            if [[ -n "$SUDO_PID" ]] && ! kill -0 "$SUDO_PID" 2>/dev/null; then
                log WARN "Sudo keepalive died; re-acquiring before $script."
                if ! init_sudo; then
                    HARD_FAILED_SCRIPTS+=("$script (sudo-unavailable)")
                    return 1
                fi
            fi
            if ! sudo -n -v 2>/dev/null; then
                log ERROR "Sudo credentials expired before $script."
                HARD_FAILED_SCRIPTS+=("$script (sudo-auth-failed)")
                FAILED_COMMAND="sudo $script"
                FAILED_EXIT=1
                return 1
            fi
        fi

        # Privilege indicator and quoted args in progress.
        printf '%s[%d/%d][%s]%s ' "$CLR_CYN" "$((i + 1))" "$total" "$mode" "$CLR_RST"

        if [[ "$OPT_DRY_RUN" == true ]]; then
            if [[ -n "$quoted_args" ]]; then
                printf '%s→%s %s %s [DRY-RUN preview]\n' "$CLR_BLU" "$CLR_RST" "$script" "$quoted_args"
            else
                printf '%s→%s %s [DRY-RUN preview]\n' "$CLR_BLU" "$CLR_RST" "$script"
            fi
            continue
        fi

        if [[ -n "$quoted_args" ]]; then
            printf '%s→%s [%s] %s %s\n' "$CLR_BLU" "$CLR_RST" "$mode" "$script" "$quoted_args"
        else
            printf '%s→%s [%s] %s\n' "$CLR_BLU" "$CLR_RST" "$mode" "$script"
        fi

        # No automatic retries by default; explicit user retry only.
        while true; do
            local rc=0

            # Actual execution with checked WORK_TREE cwd inside run_logged_command.
            # DUSKY_SCRIPT_PAYLOAD scopes interactive detection to the script file only.
            case "$mode" in
                S) DUSKY_SCRIPT_PAYLOAD="$script_path" run_logged_command sudo "${interpreter_cmd[@]}" "$script_path" "${args[@]}" || rc=$? ;;
                U) DUSKY_SCRIPT_PAYLOAD="$script_path" run_logged_command "${interpreter_cmd[@]}" "$script_path" "${args[@]}" || rc=$? ;;
                *) log ERROR "Invalid mode $mode for $script"; rc=1 ;;
            esac
            unset DUSKY_SCRIPT_PAYLOAD 2>/dev/null || true

            if ((rc == 0)); then
                EXECUTED_SCRIPTS+=("$script")
                break
            fi

            # SIGINT/TERM interruption is not a retryable script failure.
            if ((rc == 130 || rc == 143 || rc == 129)); then
                log ERROR "$script interrupted (exit $rc); stopping sequence and reaping helpers."
                HARD_FAILED_SCRIPTS+=("$script (interrupted exit $rc)")
                FAILED_SCRIPT_DIRS["${script_path%/*}"]=1
                FAILED_COMMAND="$script"
                FAILED_EXIT="$rc"
                INTERRUPTED=true
                stop_sudo || true
                return "$rc"
            fi

            if [[ "$ignore_fail" == "true" ]]; then
                log WARN "$script failed (exit $rc) - ignored via ignore-fail"
                SOFT_FAILED_SCRIPTS+=("$script (exit $rc)")
                FAILED_SCRIPT_DIRS["${script_path%/*}"]=1
                break
            fi

            log ERROR "$script failed (exit $rc) [command: $script, exit: $rc]"

            # --stop-on-fail takes effect immediately even with TTY.
            if [[ "$OPT_STOP_ON_FAIL" == true ]]; then
                log ERROR "Stopping execution sequence due to --stop-on-fail (required failure exit $rc)"
                HARD_FAILED_SCRIPTS+=("$script (exit $rc)")
                FAILED_SCRIPT_DIRS["${script_path%/*}"]=1
                FAILED_COMMAND="$script"
                FAILED_EXIT="$rc"
                return 1
            fi

            if [[ -t 0 && "$OPT_FORCE" != true ]]; then
                local _fail_choice=""

                # 1. Drain any accidental type-ahead keystrokes to prevent instant auto-skipping
                while read -r -t 0.01; do : ; done 2>/dev/null || true

                printf '\n%s[ACTION REQUIRED]%s Script execution failed: %s (exit %d)\n' "$CLR_YLW" "$CLR_RST" "$script" "$rc"

                # 2. Split prompt across two lines to protect against stray \r from async logs wiping it
                printf 'Do you want to [S]kip, [R]etry, or [Q]uit? (safe default: Quit)\n(S/r/Q): '

                if ! read -r _fail_choice; then
                    _fail_choice="q"
                fi

                _fail_choice="${_fail_choice:-q}"

                case "${_fail_choice,,}" in
                    s|skip)
                        log WARN "Skipping $script (User Selection, exit $rc)."
                        SKIPPED_SCRIPTS+=("$script (exit $rc)")
                        FAILED_SCRIPT_DIRS["${script_path%/*}"]=1
                        FAILED_COMMAND="$script"
                        FAILED_EXIT="$rc"
                        break
                        ;;
                    r|retry)
                        # Explicit user retry only; re-validate sudo before retry.
                        if [[ "$mode" == "S" ]] && ! sudo -n -v 2>/dev/null; then
                            log ERROR "Sudo credentials expired; cannot retry $script."
                            HARD_FAILED_SCRIPTS+=("$script (sudo-auth-failed exit $rc)")
                            return 1
                        fi
                        log INFO "Retrying $script (explicit user request)..."
                        sleep 1
                        continue
                        ;;
                    *)
                        log ERROR "Stopping execution as requested (quit, exit $rc)."
                        HARD_FAILED_SCRIPTS+=("$script (exit $rc)")
                        FAILED_SCRIPT_DIRS["${script_path%/*}"]=1
                        FAILED_COMMAND="$script"
                        FAILED_EXIT="$rc"
                        return 1
                        ;;
                esac
            else
                # Noninteractive failure must not silently continue required steps.
                HARD_FAILED_SCRIPTS+=("$script (exit $rc)")
                FAILED_SCRIPT_DIRS["${script_path%/*}"]=1
                FAILED_COMMAND="$script"
                FAILED_EXIT="$rc"
                log ERROR "Non-interactive required failure; stopping (exit $rc). Use interactive TTY to explicitly skip/retry."
                return 1
            fi
        done
    done

    return 0
}

# ==============================================================================
# SUMMARY & CLEANUP
# ==============================================================================
print_summary() {
    if [[ "$SKIP_FINAL_SUMMARY" == true || "$SUMMARY_PRINTED" == true ]]; then
        return 0
    fi
    SUMMARY_PRINTED=true

    local duration=$SECONDS
    local minutes=$((duration / 60))
    local seconds=$((duration % 60))
    local failed_phase_display="${FAILED_PHASE:-$CURRENT_PHASE}"

    printf '\n'
    log SECTION "Summary"

    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "Dry-run preview complete — no changes were made (read-only, non-interactive)."
        log INFO "Pre-sync preview only; post-sync scripts unavailable until sync completes."
    fi

    if [[ "$SYNC_FAILED" == true ]]; then
        log WARN "Sync phase did not complete successfully (phase: $failed_phase_display)."
    fi

    if [[ -n "$FAILED_COMMAND" ]]; then
        log ERROR "Failed command: $FAILED_COMMAND (exit $FAILED_EXIT, phase: $failed_phase_display)"
    elif ((${#HARD_FAILED_SCRIPTS[@]} > 0)) && ((FAILED_EXIT != 0)); then
        log ERROR "Failed phase: $failed_phase_display (exit $FAILED_EXIT)"
    elif [[ -n "$failed_phase_display" && "$failed_phase_display" != "summary" && "$failed_phase_display" != "script execution" && "$OPT_DRY_RUN" != true ]]; then
        # Startup/preflight/sync failures are not success even if no hard scripts listed.
        :
    fi

    if ((${#HARD_FAILED_SCRIPTS[@]} > 0)); then
        log ERROR "${#HARD_FAILED_SCRIPTS[@]} required script(s) failed:"
        local fs=""
        for fs in "${HARD_FAILED_SCRIPTS[@]}"; do
            log RAW "    • $fs"
        done
    elif [[ "$SYNC_FAILED" != true && "$OPT_DRY_RUN" != true && "$INTERRUPTED" != true && "$EXECUTE_RC" == 0 && ( "$CURRENT_PHASE" == "script execution" || "$CURRENT_PHASE" == "summary" || "$CURRENT_PHASE" == "cleanup" ) && -z "$FAILED_PHASE" && ((${#SKIPPED_SCRIPTS[@]} == 0)) ]]; then
        # Only claim success when actually completed, not after startup/preflight failure.
        # Explicitly skipped required steps must not produce success.
        if ((${#SOFT_FAILED_SCRIPTS[@]} > 0)) || [[ -n "$MERGE_DIR" && -d "$MERGE_DIR" ]]; then
            log WARN "Completed with warnings (see below); not unqualified success."
        else
            log OK "All required operations completed successfully."
        fi
    elif (( ${#SKIPPED_SCRIPTS[@]} > 0 )) && [[ -z "$FAILED_PHASE" ]]; then
        log ERROR "Did not complete successfully (${#SKIPPED_SCRIPTS[@]} required step(s) explicitly skipped)."
        [[ -z "$FAILED_PHASE" ]] && FAILED_PHASE="script execution"
    elif [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "Dry-run preview only; no success claim for hypothetical operations."
    elif [[ -n "$FAILED_PHASE" ]]; then
        log ERROR "Did not complete successfully (failed phase: $FAILED_PHASE)."
    fi

    if ((${#SOFT_FAILED_SCRIPTS[@]} > 0)); then
        log WARN "${#SOFT_FAILED_SCRIPTS[@]} script(s) soft failed / warnings (ignore-fail):"
        local fs=""
        for fs in "${SOFT_FAILED_SCRIPTS[@]}"; do
            log RAW "    • $fs"
        done
    fi

    if ((${#SKIPPED_SCRIPTS[@]} > 0)); then
        log INFO "${#SKIPPED_SCRIPTS[@]} script(s) explicitly skipped by user:"
        local fs=""
        for fs in "${SKIPPED_SCRIPTS[@]}"; do
            log RAW "    • $fs"
        done
    fi

    if ((${#EXECUTED_SCRIPTS[@]} > 0)); then
        log OK "${#EXECUTED_SCRIPTS[@]} script(s) executed successfully."
    fi

    if [[ -n "$MERGE_DIR" && -d "$MERGE_DIR" ]]; then
        log WARN "MANUAL MERGE WORK OUTSTANDING — preserved at: $MERGE_DIR"
        log WARN "  Review files in: ${MERGE_DIR}/files and markers in: ${MERGE_DIR}/deletion-markers"
    fi

    log INFO "Execution Time: ${minutes}m ${seconds}s"

    if ((${#FAILED_SCRIPT_DIRS[@]} > 0)); then
        log INFO "You can run the missing scripts individually from their respective directories:"
        local -a sorted_dirs=()
        local tmp_sort=""
        tmp_sort="$(mktemp)" || true
        if [[ -n "$tmp_sort" ]]; then
            printf '%s\0' "${!FAILED_SCRIPT_DIRS[@]}" | sort -z >"$tmp_sort" 2>/dev/null || true
            mapfile -d '' -t sorted_dirs <"$tmp_sort" 2>/dev/null || true
            rm -f -- "$tmp_sort" 2>/dev/null || true
        fi
        local fdir=""
        for fdir in "${sorted_dirs[@]}"; do
            if [[ -d "$fdir" ]]; then
                log RAW "    • ${fdir}/"
            fi
        done
    fi

    if [[ -n "$USER_MODS_BACKUP_DIR" && -d "$USER_MODS_BACKUP_DIR" ]]; then
        log INFO "User-mods recovery backup: $USER_MODS_BACKUP_DIR (complete: $USER_MODS_BACKUP_COMPLETE)"
    fi
    if ((${#COLLISION_BACKUP_DIRS[@]} > 0)); then
        local cdir=""
        for cdir in "${COLLISION_BACKUP_DIRS[@]}"; do
            [[ -d "$cdir" ]] && log INFO "Collision recovery backup: $cdir"
        done
    fi
    if [[ -n "$FULL_TRACKED_BACKUP_DIR" && -d "$FULL_TRACKED_BACKUP_DIR" ]]; then
        log INFO "Full tracked recovery backup: $FULL_TRACKED_BACKUP_DIR (complete: $FULL_TRACKED_BACKUP_COMPLETE)"
    fi
    if [[ -n "$GIT_HISTORY_BACKUP_DIR" && -d "$GIT_HISTORY_BACKUP_DIR" ]]; then
        log INFO "Git history recovery backup: $GIT_HISTORY_BACKUP_DIR (complete: $GIT_HISTORY_BACKUP_COMPLETE)"
    fi

    if [[ -n "$LOG_FILE" ]]; then
        log INFO "Log saved to: $LOG_FILE"
    fi
}

cleanup_temp_dirs_safely() {
    local tdir="" status="" displaced=""

    if ((${#CREATED_TEMP_DIRS[@]} == 0)); then
        return 0
    fi
    for tdir in "${CREATED_TEMP_DIRS[@]}"; do
        [[ -n "$tdir" ]] || continue
        status="${CREATED_TEMP_STATUS["$tdir"]:-unknown}"
        displaced="${TEMP_DISPLACED_FOR_TMPDIR["$tdir"]:-}"
        case "$status" in
            completed-atomic)
                rm -rf -- "$tdir" 2>/dev/null || log WARN "Failed to clean temp dir: $tdir"
                ;;
            completed-journaled)
                # Displaced holds upstream version (recoverable via Git), but only
                # delete when target exists; otherwise preserve.
                if [[ -n "$displaced" && ! -e "$displaced" && ! -L "$displaced" ]]; then
                    rm -rf -- "$tdir" 2>/dev/null || log WARN "Failed to clean temp dir: $tdir"
                elif [[ -n "$displaced" ]]; then
                    log WARN "Preserving journaled temp dir with displaced original for review: $tdir (displaced: $displaced)"
                else
                    rm -rf -- "$tdir" 2>/dev/null || true
                fi
                ;;
            rolled-back|failed-atomic|failed-copy|failed-displace|failed-restore|failed-*|displaced|active|unknown)
                # NEVER delete the only displaced original after failed rollback.
                if [[ -n "$displaced" && (-e "$displaced" || -L "$displaced") ]]; then
                    log WARN "Preserving temp dir with recovery data (status $status): $tdir"
                elif [[ -d "$tdir" ]]; then
                    # No displaced original; safe to clean active/failed copies? Preserve failed for diagnosis.
                    if [[ "$status" == active || "$status" == unknown ]]; then
                        rm -rf -- "$tdir" 2>/dev/null || true
                    else
                        log WARN "Preserving failed temp dir for diagnosis (status $status): $tdir"
                    fi
                fi
                ;;
            *)
                log WARN "Preserving temp dir with unknown status ($status): $tdir"
                ;;
        esac
    done
}

write_self_update_handoff() {
    local handoff=""
    handoff="$(make_private_file_under "$ACTIVE_BACKUP_BASE_DIR" "selfupdate_handoff_${RUN_TIMESTAMP}_XXXXXX.env")" || {
        log ERROR "Failed to create self-update handoff file"
        return 1
    }
    {
        declare -p LOG_FILE ACTIVE_LOG_BASE_DIR ACTIVE_BACKUP_BASE_DIR 2>/dev/null | sed 's/^declare --/declare -g --/'
        printf 'DUSKY_HANDOFF_RUN_TIMESTAMP=%q\n' "$RUN_TIMESTAMP"
        declare -p USER_MODS_BACKUP_DIR USER_MODS_BACKUP_COMPLETE FULL_TRACKED_BACKUP_DIR FULL_TRACKED_BACKUP_COMPLETE GIT_HISTORY_BACKUP_DIR GIT_HISTORY_BACKUP_COMPLETE MERGE_DIR PRE_SYNC_HEAD PRE_SYNC_HEAD_OID PINNED_REMOTE_OID 2>/dev/null | sed 's/^declare --/declare -g --/'
        declare -p COLLISION_BACKUP_DIRS 2>/dev/null | sed 's/^declare -a/declare -ga/'
        printf 'DUSKY_HANDOFF_VERSION=%q\n' "1"
        printf 'DUSKY_HANDOFF_SELF_HASH=%q\n' "$(file_sha256 "$SELF_PATH" 2>/dev/null || printf 'unknown')"
    } >"$handoff" || {
        log ERROR "Failed to write self-update handoff file"
        return 1
    }
    chmod 600 -- "$handoff" 2>/dev/null || true
    HANDOFF_FILE="$handoff"
    printf '%s' "$handoff"
}

load_self_update_handoff() {
    local handoff="${DUSKY_HANDOFF_FILE:-}"
    local cur_hash="" file_hash="" file_version="" mode="" line=""
    # Narrow validation BEFORE interpreting: private owned regular file only.
    [[ -n "$handoff" ]] || return 1
    [[ -f "$handoff" && ! -L "$handoff" ]] || { log ERROR "Handoff is not a regular file: $handoff"; return 1; }
    [[ -O "$handoff" ]] || { log ERROR "Handoff not owned by current user: $handoff"; return 1; }
    [[ -r "$handoff" ]] || return 1
    mode="$(stat -c '%a' -- "$handoff" 2>/dev/null || printf '000')"
    [[ "$mode" == "600" ]] || { log ERROR "Handoff has unsafe permissions ($mode), expected 600: $handoff"; return 1; }
    # Validate schema/content data-only before sourcing: version + hash + expected paths.
    file_version="$(grep -E '^DUSKY_HANDOFF_VERSION=1$' -- "$handoff" 2>/dev/null || true)"
    [[ -n "$file_version" ]] || { log ERROR "Handoff version mismatch or missing (expected 1)."; return 1; }
    file_hash="$(sed -n 's/^DUSKY_HANDOFF_SELF_HASH=//p' -- "$handoff" 2>/dev/null | head -n1)"
    # Hash is %q-quoted; unquote single level for compare (writer uses %q).
    # Compare against current script hash to reject stale handoffs.
    cur_hash="$(file_sha256 "$SELF_PATH" 2>/dev/null || printf 'unknown')"
    # Normalize both (strip surrounding quotes if present from %q).
    file_hash="${file_hash#\'}"; file_hash="${file_hash%\'}"
    # %q for hex is unquoted, direct compare is fine; unknown never matches.
    if [[ -z "$cur_hash" || "$cur_hash" == "unknown" || -z "$file_hash" || "$cur_hash" != "$file_hash" ]]; then
        log ERROR "Handoff script hash mismatch (stale handoff); rejecting."
        return 1
    fi
    # Reject any line outside narrow data-only schema before sourcing.
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            'declare -g -- LOG_FILE='*|'declare -g -- ACTIVE_'*|'declare -g -- USER_MODS_'*|'declare -g -- FULL_TRACKED_'*|'declare -g -- GIT_HISTORY_'*|'declare -g -- MERGE_DIR='*|'declare -g -- PRE_SYNC_'*|'declare -g -- PINNED_REMOTE_'*|'declare -ga COLLISION_BACKUP_DIRS='*|'DUSKY_HANDOFF_RUN_TIMESTAMP='*|'DUSKY_HANDOFF_VERSION='*|'DUSKY_HANDOFF_SELF_HASH='*) ;;
            ''|'#*') ;;
            *) log ERROR "Handoff contains unexpected content; rejecting."; return 1 ;;
        esac
    done <"$handoff"
    # shellcheck disable=SC1090
    if ! source -- "$handoff" 2>/dev/null; then
        log ERROR "Handoff failed to load after validation."
        return 1
    fi
    [[ -n "${LOG_FILE:-}" && -n "${ACTIVE_BACKUP_BASE_DIR:-}" ]] || return 1
    [[ -f "$LOG_FILE" && -w "$LOG_FILE" ]] || { log ERROR "Handoff recovery log unusable."; return 1; }
    [[ -d "${ACTIVE_BACKUP_BASE_DIR:-}" && -O "${ACTIVE_BACKUP_BASE_DIR:-}" ]] || { log ERROR "Handoff backup dir unusable."; return 1; }
    HANDOFF_FILE="$handoff"
    # Consume/retire: delete after successful load to reject reuse (no locks).
    rm -f -- "$handoff" 2>/dev/null || true
    unset DUSKY_HANDOFF_FILE 2>/dev/null || true
    return 0
}

cleanup() {
    local rc=$?
    local failed_phase="$CURRENT_PHASE"
    # Preserve incoming status and failed phase; do not overwrite with cleanup success.
    if [[ -z "$FAILED_PHASE" ]] && ((rc != 0)); then
        FAILED_PHASE="$failed_phase"
    fi
    CURRENT_PHASE="cleanup"

    reap_logging_processes || true
    stop_sudo || true

    cleanup_temp_dirs_safely || true

    if [[ -n "$USER_MODS_BACKUP_DIR" && -d "$USER_MODS_BACKUP_DIR" ]]; then
        printf '\n'
        log WARN "Update was incomplete. Your modified files are preserved at:"
        printf '    %s\n' "$USER_MODS_BACKUP_DIR"
        log WARN "Backup completion: $USER_MODS_BACKUP_COMPLETE (partial vs completed)"
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
        log INFO "Completion: $FULL_TRACKED_BACKUP_COMPLETE"
    fi

    if [[ -n "$GIT_HISTORY_BACKUP_DIR" && -d "$GIT_HISTORY_BACKUP_DIR" ]]; then
        log INFO "Git history backup preserved at:"
        printf '    %s\n' "$GIT_HISTORY_BACKUP_DIR"
        log INFO "Completion: $GIT_HISTORY_BACKUP_COMPLETE"
    fi

    if [[ -n "$MERGE_DIR" && -d "$MERGE_DIR" ]]; then
        log WARN "Manual-merge work preserved at: $MERGE_DIR (files/, deletion-markers/)"
    fi

    print_summary

    if [[ "$INTERRUPTED" == true ]]; then
        desktop_notify critical "Dusky Update" "Update interrupted (phase: ${FAILED_PHASE:-$failed_phase})"
        exit "$rc"
    elif ((${#HARD_FAILED_SCRIPTS[@]} > 0)); then
        desktop_notify critical "Dusky Update" "${#HARD_FAILED_SCRIPTS[@]} required script(s) failed (phase: ${FAILED_PHASE:-$failed_phase})"
        exit 1
    elif ((rc != 0)); then
        desktop_notify critical "Dusky Update" "Update failed or interrupted (phase: ${FAILED_PHASE:-$failed_phase}, exit $rc)"
        exit "$rc"
    elif [[ "$SYNC_FAILED" == true ]]; then
        desktop_notify critical "Dusky Update" "Sync phase failed (phase: ${FAILED_PHASE:-$failed_phase})"
        exit 1
    elif (( ${#SKIPPED_SCRIPTS[@]} > 0 )); then
        desktop_notify critical "Dusky Update" "${#SKIPPED_SCRIPTS[@]} required step(s) skipped (phase: ${FAILED_PHASE:-script execution}); incomplete"
        exit 1
    elif [[ "$OPT_DRY_RUN" == true ]]; then
        desktop_notify normal "Dusky Update" "Dry-run preview complete"
        exit 0
    elif [[ -n "$MERGE_DIR" && -d "$MERGE_DIR" ]] || ((${#SOFT_FAILED_SCRIPTS[@]} > 0)); then
        desktop_notify normal "Dusky Update" "Update completed with warnings/manual-merge work outstanding; review logs"
        exit 0
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

    # Dry-run must not consume handoffs or mutate logs/backups/index/config.
    if [[ "$OPT_DRY_RUN" == true && "$OPT_POST_SELF_UPDATE" == true ]]; then
        printf 'Error: --dry-run with --post-self-update is rejected (read-only; handoffs not consumed).\n' >&2
        exit 1
    fi

    local handoff_valid=false
    if [[ "$OPT_POST_SELF_UPDATE" == true ]]; then
        if load_self_update_handoff; then
            handoff_valid=true
            log INFO "Resumed after self-update; reusing log $LOG_FILE and recovery state."
            printf '\n--- Resumed after self-update (%s) ---\n' "$RUN_TIMESTAMP" >>"$LOG_FILE" 2>/dev/null || true
        else
            log WARN "--post-self-update without valid handoff state; ignoring flag and running full sync."
            OPT_POST_SELF_UPDATE=false
            handoff_valid=false
        fi
    fi

    # Dry-run is non-interactive and read-only: never prompt at startup.
    if [[ -t 0 && "$OPT_FORCE" != true && "$OPT_POST_SELF_UPDATE" != true && "$OPT_DRY_RUN" != true ]]; then
        printf '\n%sNote:%s Avoid interrupting the update while it'\''s running.\n' "${CLR_YLW}" "${CLR_RST}"
        printf 'Interruptions during git operations can leave the repository in a broken state.\n\n'
        local start_confirm=""
        if ! read -r -p "Start the update? [y/N] " start_confirm; then
            start_confirm="n"
        fi
        if [[ ! "$start_confirm" =~ ^[Yy]$ ]]; then
            printf 'Update cancelled.\n'
            exit 0
        fi
    fi

    trap cleanup EXIT
    trap 'INTERRUPTED=true; FAILED_PHASE="${CURRENT_PHASE}"; log WARN "Interrupted by user (SIGINT)"; exit 130' INT
    trap 'INTERRUPTED=true; FAILED_PHASE="${CURRENT_PHASE}"; log WARN "Terminated (SIGTERM)"; exit 143' TERM
    trap 'log WARN "Hangup signal received (SIGHUP)"; exit 129' HUP

    if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "Running in DRY-RUN mode — preview only, no changes, non-interactive"
        # Dry-run still needs storage roots for preview paths? Keep read-only:
        # do not create logs/backups, do not prune, do not prompt.
        if [[ "$handoff_valid" != true ]]; then
            ACTIVE_LOG_BASE_DIR="$LOG_BASE_DIR"
            ACTIVE_BACKUP_BASE_DIR="$BACKUP_BASE_DIR"
        fi
    else
        if [[ "$handoff_valid" == true ]]; then
            # Reuse handoff log/state; ensure dirs still valid, prune disposable logs only.
            ensure_storage_dir "$ACTIVE_LOG_BASE_DIR" || {
                printf 'Error: Handoff log dir unusable: %s\n' "$ACTIVE_LOG_BASE_DIR" >&2
                exit 1
            }
            ensure_storage_dir "$ACTIVE_BACKUP_BASE_DIR" || {
                printf 'Error: Handoff backup dir unusable: %s\n' "$ACTIVE_BACKUP_BASE_DIR" >&2
                exit 1
            }
            auto_prune || true
        else
            setup_storage_roots
            setup_logging
            auto_prune
        fi
    fi

    local self_hash_before=""
    if [[ "$OPT_DRY_RUN" != true && "$OPT_POST_SELF_UPDATE" != true && -r "$SELF_PATH" ]]; then
        self_hash_before="$(file_sha256 "$SELF_PATH" || true)"
    fi

    local cont="n"
    local sync_rc=0
    local syntax_rc=0 handoff_rc=0 exec_rc=0

    CURRENT_PHASE="sync"
    if [[ "$OPT_SKIP_SYNC" != true && "$OPT_POST_SELF_UPDATE" != true ]]; then
        if pull_updates; then
            if [[ "$OPT_DRY_RUN" != true && -n "$self_hash_before" && -r "$SELF_PATH" ]]; then
                local self_hash_after=""
                self_hash_after="$(file_sha256 "$SELF_PATH" || true)"
                if [[ -n "$self_hash_after" && "$self_hash_before" != "$self_hash_after" ]]; then
                    log SECTION "Self-Update Detected"
                    log INFO "Validating updated script syntax before reexec..."
                    if "$BASH_BIN" -n "$SELF_PATH"; then
                        : # syntax OK, continue below
                    else
                        syntax_rc=$?
                        log ERROR "Updated script failed syntax check (exit $syntax_rc); aborting."
                        FAILED_PHASE="self-reexec"
                        return "$syntax_rc"
                    fi
                        log OK "Updated script syntax OK; preserving run/recovery state across reexec (no locking)..."
                        if write_self_update_handoff >/dev/null; then
                            : # handoff OK
                        else
                            handoff_rc=$?
                            log ERROR "Failed to preserve handoff state (exit $handoff_rc); aborting reexec."
                            FAILED_PHASE="self-reexec"
                            return "$handoff_rc"
                        fi
                            CURRENT_PHASE="self-reexec"
                            SKIP_FINAL_SUMMARY=true
                            stop_sudo || true

                            local -a reexec_args=("--post-self-update")
                            [[ "$OPT_FORCE" == true ]] && reexec_args+=("--force")
                            [[ "$OPT_SKIP_SYNC" == true ]] && reexec_args+=("--skip-sync")
                            [[ "$OPT_SYNC_ONLY" == true ]] && reexec_args+=("--sync-only")
                            [[ "$OPT_STOP_ON_FAIL" == true ]] && reexec_args+=("--stop-on-fail")
                            [[ "$OPT_ALLOW_DIVERGED_RESET" == true ]] && reexec_args+=("--allow-diverged-reset")

                            export DUSKY_HANDOFF_FILE="$HANDOFF_FILE"
                            # shellcheck disable=SC2093
                            if exec "$BASH_BIN" "$SELF_PATH" "${reexec_args[@]}"; then
                                : # exec replaces process; never returns on success
                            else
                                exec_rc=$?
                                SKIP_FINAL_SUMMARY=false
                                log ERROR "Self-reexec exec failed (exit $exec_rc); handoff preserved at $HANDOFF_FILE."
                                FAILED_PHASE="self-reexec"
                                return "$exec_rc"
                            fi
                fi
            fi
        else
            sync_rc=$?
            SYNC_FAILED=true
            FAILED_PHASE="sync"
            log WARN "Sync failed (exit $sync_rc, phase sync)."

            if [[ "$OPT_SYNC_ONLY" == true ]]; then
                exit 1
            fi

            if ((sync_rc == SYNC_RC_RECOVERABLE)) && [[ -t 0 && "$OPT_FORCE" != true && "$OPT_DRY_RUN" != true ]]; then
                if ! read -r -t "$PROMPT_TIMEOUT_SHORT" -p "Continue with local scripts? [y/N] " cont; then
                    cont="n"
                fi
            else
                cont="n"
            fi

            [[ "$cont" =~ ^[Yy]$ ]] || exit 1
        fi
    elif [[ "$OPT_POST_SELF_UPDATE" == true && "$handoff_valid" == true ]]; then
        log INFO "Post-self-update: skipping sync (already synced before reexec)."
    fi

    if [[ "$OPT_SYNC_ONLY" == true ]]; then
        log OK "Sync-only mode — skipping script execution."
    elif [[ "$SYNC_FAILED" != true || "$cont" =~ ^[Yy]$ ]]; then
        CURRENT_PHASE="preflight"
        parse_update_sequence_manifest
        validate_search_dirs || { FAILED_PHASE="preflight"; exit 1; }
        resolve_and_validate_manifest || { FAILED_PHASE="preflight"; exit 1; }
        require_sudo_if_needed || { FAILED_PHASE="preflight"; exit 1; }

        CURRENT_PHASE="script execution"
        EXECUTE_RC=0
        if execute_scripts; then
            EXECUTE_RC=0
            if (( ${#SKIPPED_SCRIPTS[@]} > 0 )); then
                FAILED_PHASE="script execution"
                FAILED_COMMAND="${FAILED_COMMAND:-skipped required steps}"
                [[ "$FAILED_EXIT" == "0" ]] && FAILED_EXIT=1
            fi
        else
            EXECUTE_RC=$?
            FAILED_PHASE="script execution"
            FAILED_COMMAND="${FAILED_COMMAND:-script execution}"
            # Explicitly propagate failures and signals; do not swallow with || true.
            if ((EXECUTE_RC == 129 || EXECUTE_RC == 130 || EXECUTE_RC == 143)); then
                INTERRUPTED=true
                return "$EXECUTE_RC"
            fi
            return "$EXECUTE_RC"
        fi
    fi

    CURRENT_PHASE="summary"
}

main
