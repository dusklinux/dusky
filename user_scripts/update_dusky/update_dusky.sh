#!/usr/bin/env bash
# ==============================================================================
#  DUSKY UPDATER (v7.0 — File-Based Backup/Restore)
#  Description: Manages dotfile/system updates while preserving user tweaks.
#               Uses file-based backup instead of git stash to prevent config
#               corruption from conflict markers.
#  Target:      Arch Linux / Hyprland / UWSM / Bash 5.0+
#  Repo Type:   Git Bare Repository (--git-dir=~/dusky --work-tree=~)
# ==============================================================================

set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true
shopt -s extglob 2>/dev/null || true

if ((BASH_VERSINFO[0] < 5)); then
  printf 'Error: Bash 5.0+ required (found %s)\n' "$BASH_VERSION" >&2
  exit 1
fi

# ==============================================================================
# CONSTANTS — Timeouts, limits, and tuning parameters
# ==============================================================================
declare -ri SUDO_KEEPALIVE_INTERVAL=55 # seconds between sudo refreshes (sudo timeout is typically 300s)
declare -ri FETCH_TIMEOUT=60           # seconds before a single git fetch attempt is killed
declare -ri FETCH_MAX_ATTEMPTS=5       # maximum number of fetch retries
declare -ri FETCH_INITIAL_BACKOFF=2    # seconds to wait after first fetch failure (doubles each retry)
declare -ri PROMPT_TIMEOUT_LONG=60     # seconds for major decision prompts
declare -ri PROMPT_TIMEOUT_SHORT=30    # seconds for minor continue/abort prompts
declare -ri STRIP_ANSI_MAX_ITER=100    # safety limit for strip_ansi regex loop (unused with extglob method)
declare -ri LOG_RETENTION_DAYS=14      # auto-delete logs older than this
declare -ri BACKUP_RETENTION_DAYS=14   # auto-delete backups older than this
declare -ri DISK_MIN_FREE_MB=100       # minimum free disk space (MB) before aborting backup operations
declare -r VERSION="7.0"

# ==============================================================================
# CONFIGURATION — Core paths and repository settings
# ==============================================================================
declare -r DOTFILES_GIT_DIR="${HOME}/dusky"
declare -r WORK_TREE="${HOME}"
declare -r SCRIPT_DIR="${HOME}/user_scripts/arch_setup_scripts/scripts"
declare -r LOG_BASE_DIR="${HOME}/Documents/logs"
declare -r BACKUP_BASE_DIR="${HOME}/Documents/dusky_backups"
declare -r LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/dusky-updater-$(id -u).lock"
declare -r REPO_URL="https://github.com/dusklinux/dusky"
declare -r BRANCH="main"

# ==============================================================================
# USER CONFIGURATION — Custom script paths and update sequence
# ==============================================================================

# ------------------------------------------------------------------------------
# CUSTOM SCRIPT PATHS
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

declare -A CUSTOM_SCRIPT_PATHS=(
  # Example:
  # ["warp_toggle.sh"]="user_scripts/networking/warp_toggle.sh"
  # Then in UPDATE_SEQUENCE add: "S | warp_toggle.sh"

  ["warp_toggle.sh"]="user_scripts/networking/warp_toggle.sh"
  ["fix_theme_dir.sh"]="user_scripts/misc_extra/fix_theme_dir.sh"
  ["package_installation.sh"]="user_scripts/misc_extra/package_installation.sh"
  ["copy_service_files.sh"]="user_scripts/misc_extra/copy_service_files.sh"
  ["update_checker.sh"]="user_scripts/update_dusky/update_checker/update_checker.sh"
  ["cc_restart.sh"]="user_scripts/dusky_system/reload_cc/cc_restart.sh"
  ["dusky_service_manager.sh"]="user_scripts/services/dusky_service_manager.sh"
  ["user_prefs_ctl.sh"]="user_scripts/theme_matugen/user_prefs_ctl.sh"
)

# ------------------------------------------------------------------------------
# UPDATE SEQUENCE — Scripts to execute after sync
# ------------------------------------------------------------------------------
declare -ra UPDATE_SEQUENCE=(
  "U | 005_hypr_custom_config_setup.sh"
  "U | 010_package_removal.sh --auto"
  "U | 015_set_thunar_terminal_kitty.sh"
  "U | 020_desktop_apps_username_setter.sh --quiet"
  #    "U | 025_configure_keyboard.sh"
  "U | 035_configure_uwsm_gpu.sh --auto"
  #    "U | 040_long_sleep_timeout.sh"
  #    "S | 045_battery_limiter.sh"
  #    "S | 050_pacman_config.sh --auto"
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
  #    "U | 160_theme_ctl.sh set --defaults"
  #    "U | 165_qtct_config.sh"
  "U | 170_waypaper_config_reset.sh"
  "U | user_prefs_ctl.sh --save"
  "U | 175_animation_default.sh"
  "U | user_prefs_ctl.sh --restore"
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
  #    "U | 235_file_manager_switch.sh --apply-state"
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
  #    "U | 435_key_sound_wayclick_setup.sh --setup"
  #    "U | 440_config_bat_notify.sh --default"
  #    "U | 450_generate_colorfiles_for_current_wallpaer.sh"
  "U | 455_hyprctl_reload.sh"
  "U | 460_switch_clipboard.sh --terminal --force"
  #    "S | 465_sddm_setup.sh"
  #    "U | 470_vesktop_matugen.sh"
  #    "U | 475_reverting_sleep_timeout.sh"

  #================= CUSTOM=====================

  "U | copy_service_files.sh --default"
  "U | update_checker.sh --num"
  "S | package_installation.sh"
  "U | cc_restart.sh --quiet"
  "S | dusky_service_manager.sh"
)

# ==============================================================================
# END OF USER CONFIGURATION — Do not edit below unless you know what you're doing
# ==============================================================================

# ==============================================================================
# RUNTIME STATE — Initialized before trap registration
# ==============================================================================

# Centralized timestamp
declare RUN_TIMESTAMP
RUN_TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
readonly RUN_TIMESTAMP

# Resolve self path with fallbacks
declare SELF_PATH
SELF_PATH="$(realpath -- "$0" 2>/dev/null || readlink -f -- "$0" 2>/dev/null || printf '%s' "$0")"
readonly SELF_PATH

# Binary validation
declare GIT_BIN BASH_BIN
GIT_BIN="$(command -v git 2>/dev/null)" || GIT_BIN=""
BASH_BIN="$(command -v bash 2>/dev/null)" || BASH_BIN=""

if [[ -z "$GIT_BIN" || ! -x "$GIT_BIN" ]]; then
  printf 'Error: git not found\n' >&2
  exit 1
fi
if [[ -z "$BASH_BIN" || ! -x "$BASH_BIN" ]]; then
  printf 'Error: bash not found\n' >&2
  exit 1
fi
readonly GIT_BIN BASH_BIN

# Cache username (avoid forking id -un when $USER is unset)
declare CACHED_USER
CACHED_USER="${USER:-$(id -un)}"
readonly CACHED_USER

# Git command array — initialized here so it's available globally
declare -a GIT_CMD=("$GIT_BIN" --git-dir="$DOTFILES_GIT_DIR" --work-tree="$WORK_TREE")

# Mutable runtime state — all declared before trap so cleanup() is safe under set -u
declare SUDO_PID="" LOG_FILE="" ORIGINAL_EXIT_CODE=0
declare USER_MODS_BACKUP="" PRE_UPDATE_HEAD=""
declare -a FAILED_SCRIPTS=() MODIFIED_FILES=()

# CLI flags (defaults — overridden by parse_args)
declare OPT_DRY_RUN=false
declare OPT_SKIP_SYNC=false
declare OPT_SYNC_ONLY=false
declare OPT_FORCE=false
declare OPT_STOP_ON_FAIL=false
declare OPT_POST_SELF_UPDATE=false
declare OPT_NEEDS_SUDO=false

# ==============================================================================
# ARGUMENT PARSING — Before anything else so --help exits immediately
# ==============================================================================
show_help() {
  cat <<'HELPEOF'
Dusky Updater — Dotfile sync and setup tool for Arch Linux / Hyprland

Usage: dusky_updater.sh [OPTIONS]

Options:
  --help, -h          Show this help message and exit
  --version           Show version and exit
  --dry-run           Preview what would happen without making changes
  --skip-sync         Skip git sync, only run the script sequence
  --sync-only         Pull updates but don't run scripts
  --force             Skip the confirmation prompt
  --stop-on-fail      Abort the script sequence on first failure
  --list              List all active scripts in the update sequence

The update sequence and custom script paths are configured inside this
script in the USER CONFIGURATION section.

Logs are saved to: ~/Documents/logs/
Backups are saved to: ~/Documents/dusky_backups/
HELPEOF
}

show_version() {
  printf 'Dusky Updater v%s\n' "$VERSION"
}

list_active_scripts() {
  printf 'Active scripts in update sequence:\n\n'
  local entry mode script_part idx=0
  local -a parts
  for entry in "${UPDATE_SEQUENCE[@]}"; do
    [[ "$entry" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${entry//[[:space:]]/}" ]] && continue
    mode="${entry%%|*}"
    mode="${mode#"${mode%%[![:space:]]*}"}"
    mode="${mode%"${mode##*[![:space:]]}"}"
    script_part="${entry#*|}"
    script_part="${script_part#"${script_part%%[![:space:]]*}"}"
    read -ra parts <<<"$script_part"
    ((idx++)) || true
    printf '  %3d) [%s] %s\n' "$idx" "$mode" "${parts[*]}"
  done
  printf '\nTotal: %d active script(s)\n' "$idx"
}

parse_args() {
  while (($# > 0)); do
    case "$1" in
    --help | -h)
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
    --list)
      list_active_scripts
      exit 0
      ;;
    --post-self-update)
      # Internal flag: skip sync after self-update re-exec
      OPT_POST_SELF_UPDATE=true
      ;;
    -*)
      printf 'Unknown option: %s\n' "$1" >&2
      printf 'Try --help for usage information.\n' >&2
      exit 1
      ;;
    *)
      printf 'Unexpected argument: %s\n' "$1" >&2
      printf 'Try --help for usage information.\n' >&2
      exit 1
      ;;
    esac
    shift
  done

  # Mutually exclusive flags
  if [[ "$OPT_SKIP_SYNC" == true && "$OPT_SYNC_ONLY" == true ]]; then
    printf 'Error: --skip-sync and --sync-only are mutually exclusive\n' >&2
    exit 1
  fi
}

# Parse arguments immediately
parse_args "$@"

# ==============================================================================
# DEPENDENCY CHECK
# ==============================================================================
check_dependencies() {
  local -a missing=()
  local cmd

  for cmd in flock sha256sum comm timeout; do
    command -v "$cmd" &>/dev/null || missing+=("$cmd")
  done

  if ((${#missing[@]} > 0)); then
    printf 'Error: Missing required commands: %s\n' "${missing[*]}" >&2
    printf 'Install with: sudo pacman -S coreutils util-linux\n' >&2
    exit 1
  fi
}

# ==============================================================================
# TERMINAL COLORS
# ==============================================================================
if [[ -t 1 ]]; then
  declare -r CLR_RED=$'\e[1;31m' CLR_GRN=$'\e[1;32m' CLR_YLW=$'\e[1;33m'
  declare -r CLR_BLU=$'\e[1;34m' CLR_CYN=$'\e[1;36m' CLR_RST=$'\e[0m'
else
  declare -r CLR_RED="" CLR_GRN="" CLR_YLW="" CLR_BLU="" CLR_CYN="" CLR_RST=""
fi

# ==============================================================================
# LOGGING
# ==============================================================================
setup_logging() {
  if mkdir -p "$LOG_BASE_DIR" 2>/dev/null; then
    LOG_FILE="${LOG_BASE_DIR}/dusky_update_${RUN_TIMESTAMP}.log"
  else
    LOG_FILE="/tmp/dusky_update_${RUN_TIMESTAMP}.log"
  fi

  if ! touch "$LOG_FILE" 2>/dev/null; then
    LOG_FILE="/tmp/dusky_update_${RUN_TIMESTAMP}.log"
    touch "$LOG_FILE" || {
      printf 'Error: Cannot create log\n' >&2
      exit 1
    }
  fi

  {
    printf '================================================================================\n'
    printf ' DUSKY UPDATE LOG — %s\n' "$RUN_TIMESTAMP"
    printf ' Kernel: %s | User: %s\n' "$(uname -r)" "$CACHED_USER"
    printf '================================================================================\n'
  } >>"$LOG_FILE"
}

# Strip ANSI/CSI escape sequences for log file
# Uses extglob for efficient single-pass removal
strip_ansi() {
  REPLY="${1//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
}

log() {
  (($# >= 2)) || return 1
  local -r level="$1" msg="$2"
  local timestamp prefix=""

  # Builtin timestamp — no subprocess fork
  printf -v timestamp '%(%H:%M:%S)T' -1

  case "$level" in
  INFO) prefix="${CLR_BLU}[INFO ]${CLR_RST}" ;;
  OK) prefix="${CLR_GRN}[OK   ]${CLR_RST}" ;;
  WARN) prefix="${CLR_YLW}[WARN ]${CLR_RST}" ;;
  ERROR) prefix="${CLR_RED}[ERROR]${CLR_RST}" ;;
  SECTION) prefix=$'\n'"${CLR_CYN}═══════${CLR_RST}" ;;
  RAW) prefix="" ;;
  *) prefix="[$level]" ;;
  esac

  if [[ "$level" == "RAW" ]]; then
    printf '%s\n' "$msg"
  else
    printf '%s %s\n' "$prefix" "$msg"
  fi

  if [[ -n "${LOG_FILE:-}" && -w "$LOG_FILE" ]]; then
    strip_ansi "$msg"
    printf '[%s] [%-5s] %s\n' "$timestamp" "$level" "$REPLY" >>"$LOG_FILE"
  fi
}

# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================
trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

# Check available disk space (in MB) for a given path
check_disk_space() {
  local path="$1"
  local available_mb
  available_mb=$(df -BM --output=avail "$path" 2>/dev/null | tail -1 | tr -d ' M') || available_mb=0
  if ((available_mb < DISK_MIN_FREE_MB)); then
    log ERROR "Low disk space: ${available_mb}MB available at $path (need ${DISK_MIN_FREE_MB}MB)"
    return 1
  fi
  return 0
}

# Prune old logs and backups
auto_prune() {
  # Prune old logs
  if [[ -d "$LOG_BASE_DIR" ]]; then
    find "$LOG_BASE_DIR" -name 'dusky_update_*.log' -mtime "+${LOG_RETENTION_DAYS}" -delete 2>/dev/null || true
  fi

  # Prune old backups (only dusky-created directories by prefix)
  if [[ -d "$BACKUP_BASE_DIR" ]]; then
    find "$BACKUP_BASE_DIR" -mindepth 1 -maxdepth 1 -type d \
      \( -name 'pre_reset_*' \
      -o -name 'user_mods_*' \
      -o -name 'untracked_collisions_*' \
      -o -name 'needs_merge_*' \
      -o -name 'initial_conflicts_*' \) \
      -mtime "+${BACKUP_RETENTION_DAYS}" \
      -exec rm -rf {} + 2>/dev/null || true
  fi
}

# Quick network reachability check (DNS resolution only — no ICMP which may be blocked)
check_network() {
  # Extract hostname from REPO_URL
  local host
  host="${REPO_URL#*://}"
  host="${host%%/*}"
  host="${host%%:*}"

  if ! getent hosts "$host" &>/dev/null; then
    log ERROR "Cannot resolve $host — check your network connection."
    return 1
  fi
  return 0
}

# Scan UPDATE_SEQUENCE for active S-mode scripts
scan_needs_sudo() {
  local entry mode
  for entry in "${UPDATE_SEQUENCE[@]}"; do
    [[ "$entry" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${entry//[[:space:]]/}" ]] && continue
    mode="${entry%%|*}"
    mode="${mode#"${mode%%[![:space:]]*}"}"
    mode="${mode%"${mode##*[![:space:]]}"}"
    if [[ "$mode" == "S" ]]; then
      OPT_NEEDS_SUDO=true
      return 0
    fi
  done
  OPT_NEEDS_SUDO=false
  return 0
}

# Send desktop notification (best-effort, never fails the script)
desktop_notify() {
  local urgency="${1:-normal}" summary="$2" body="${3:-}"
  if command -v notify-send &>/dev/null; then
    timeout 3 notify-send --urgency="$urgency" --app-name="Dusky Updater" "$summary" "$body" 2>/dev/null || true
  fi
}

# ==============================================================================
# CLEANUP & SIGNAL HANDLING
# ==============================================================================
cleanup() {
  # Capture exit code IMMEDIATELY
  ORIGINAL_EXIT_CODE=$?

  # Stop sudo keepalive
  if [[ -n "${SUDO_PID:-}" ]] && kill -0 "$SUDO_PID" 2>/dev/null; then
    kill "$SUDO_PID" 2>/dev/null || true
    wait "$SUDO_PID" 2>/dev/null || true
  fi

  # Inform user about backed-up modifications (if any)
  if [[ -n "${USER_MODS_BACKUP:-}" && -d "${USER_MODS_BACKUP:-}" ]]; then
    local backup_file_count
    backup_file_count=$(find "$USER_MODS_BACKUP" -type f 2>/dev/null | wc -l) || backup_file_count=0
    if ((backup_file_count > 0)); then
      printf '\n'
      log WARN "Update was interrupted. Your modified files are safely backed up at:"
      printf '    %s\n' "$USER_MODS_BACKUP"
      log INFO "You can restore them manually by copying files from that directory."
    fi
  fi

  # Clean up any partial atomic writes from interrupted restore
  if [[ -v MODIFIED_FILES ]] && ((${#MODIFIED_FILES[@]} > 0)); then
    local tmp_file
    for tmp_file in "${MODIFIED_FILES[@]}"; do
      rm -f "${WORK_TREE}/${tmp_file}.dusky_tmp" 2>/dev/null || true
    done
  fi

  # Release lock (close fd first, then remove file)
  exec 9>&- 2>/dev/null || true
  rm -f "$LOCK_FILE" 2>/dev/null || true

  printf '\n'
  if [[ -v FAILED_SCRIPTS ]] && ((${#FAILED_SCRIPTS[@]} > 0)); then
    log WARN "Completed with ${#FAILED_SCRIPTS[@]} failure(s)"
    local script
    for script in "${FAILED_SCRIPTS[@]}"; do
      printf '    • %s\n' "$script"
    done
    desktop_notify critical "Dusky Update" "${#FAILED_SCRIPTS[@]} script(s) failed"
  elif [[ -n "${LOG_FILE:-}" ]]; then
    log OK "Complete. Log: $LOG_FILE"
    if ((ORIGINAL_EXIT_CODE == 0)); then
      desktop_notify normal "Dusky Update" "Update completed successfully"
    fi
  fi

  exit "$ORIGINAL_EXIT_CODE"
}

trap cleanup EXIT
trap 'log WARN "Interrupted by user (SIGINT)"; exit 130' INT
trap 'log WARN "Terminated (SIGTERM)"; exit 143' TERM
trap 'log WARN "Hangup signal received (SIGHUP)"; exit 129' HUP

# ==============================================================================
# SUDO MANAGEMENT
# ==============================================================================
init_sudo() {
  log INFO "Acquiring sudo privileges..."
  sudo -v || {
    log ERROR "Sudo auth failed."
    exit 1
  }

  # Refresh sudo credentials periodically in background
  # sudo timeout is typically 300s; refreshing every 55s is well within that window
  (
    trap 'exit 0' TERM
    while kill -0 $$ 2>/dev/null; do
      sleep "$SUDO_KEEPALIVE_INTERVAL"
      sudo -n true 2>/dev/null || exit 0
    done
  ) &
  SUDO_PID=$!
  disown "$SUDO_PID" 2>/dev/null || true
}

# ==============================================================================
# GIT HELPERS
# ==============================================================================

# Clean up any broken git state from previous interrupted runs
cleanup_git_state() {
  local rebase_dir="${DOTFILES_GIT_DIR}/rebase-merge"
  local rebase_apply="${DOTFILES_GIT_DIR}/rebase-apply"

  if [[ -d "$rebase_dir" || -d "$rebase_apply" ]]; then
    log WARN "Detected stale rebase. Aborting..."
    "${GIT_CMD[@]}" rebase --abort >>"$LOG_FILE" 2>&1 || true
    rm -rf "$rebase_dir" "$rebase_apply" 2>/dev/null || true
  fi

  if "${GIT_CMD[@]}" diff --check 2>&1 | grep -q "leftover conflict marker"; then
    log WARN "Conflict markers detected. Cleaning working tree..."
    "${GIT_CMD[@]}" checkout HEAD -- . >>"$LOG_FILE" 2>&1 || true
  fi
}

# Show preview of upstream changes
show_update_preview() {
  local local_head="$1" remote_head="$2"
  local commit_count changed_file_count

  commit_count=$("${GIT_CMD[@]}" rev-list --count "${local_head}..${remote_head}" 2>/dev/null) || commit_count="?"
  changed_file_count=$("${GIT_CMD[@]}" diff --name-only "${local_head}..${remote_head}" 2>/dev/null | wc -l) || changed_file_count="?"

  printf '\n'
  log INFO "Upstream changes:"
  printf '    Commits behind:  %s\n' "$commit_count"
  printf '    Files changed:   %s\n' "$changed_file_count"

  # Show up to 10 recent commit subjects
  if [[ "$commit_count" != "?" ]] && ((commit_count > 0)); then
    printf '\n    Recent commits:\n'
    "${GIT_CMD[@]}" log --oneline --no-decorate -10 "${local_head}..${remote_head}" 2>/dev/null | while IFS= read -r line; do
      printf '      %s\n' "$line"
    done || true
    if ((commit_count > 10)); then
      printf '      ... and %d more\n' "$((commit_count - 10))"
    fi
  fi
  printf '\n'
}

# ==============================================================================
# BACKUP TRACKED FILES (Pre-Reset Safety Net)
# ==============================================================================
backup_tracked_files() {
  local backup_dir="${BACKUP_BASE_DIR}/pre_reset_${RUN_TIMESTAMP}"
  local tracked_files file_count=0
  local src dest coll_file

  log INFO "Backing up tracked files before reset..."

  check_disk_space "$HOME" || return 1

  tracked_files=$("${GIT_CMD[@]}" ls-files 2>/dev/null) || {
    log WARN "Could not get tracked file list. Skipping backup."
    return 1
  }

  if [[ -z "$tracked_files" ]]; then
    log WARN "No tracked files found. Skipping backup."
    return 0
  fi

  if ! mkdir -p "$backup_dir"; then
    log ERROR "Failed to create backup directory: $backup_dir"
    return 1
  fi

  while IFS= read -r coll_file; do
    [[ -z "$coll_file" ]] && continue

    src="${WORK_TREE}/${coll_file}"
    dest="${backup_dir}/${coll_file}"

    if [[ -e "$src" ]]; then
      mkdir -p "$(dirname "$dest")" 2>/dev/null || true
      if cp -a -- "$src" "$dest" 2>/dev/null; then
        ((file_count++)) || true
      fi
    fi
  done <<<"$tracked_files"

  if ((file_count > 0)); then
    log OK "Backed up $file_count tracked files to: $backup_dir"
  else
    log WARN "No files were backed up."
    rmdir "$backup_dir" 2>/dev/null || true
  fi

  return 0
}

# ==============================================================================
# BACKUP USER MODIFICATIONS (File-Based — Replaces Git Stash)
# ==============================================================================
# Copies user-modified tracked files to a backup directory BEFORE reset --hard.
# Sets USER_MODS_BACKUP on success. Returns 1 on failure (caller MUST abort).
# Idempotent: safe to call multiple times (returns 0 if already done).
backup_user_modifications() {
  # Idempotency guard
  if [[ -n "$USER_MODS_BACKUP" && -d "$USER_MODS_BACKUP" ]]; then
    return 0
  fi

  if ((${#MODIFIED_FILES[@]} == 0)); then
    return 0
  fi

  check_disk_space "$HOME" || return 1

  local backup_dir="${BACKUP_BASE_DIR}/user_mods_${RUN_TIMESTAMP}"
  local src dest mod_file
  local file_count=0

  if ! mkdir -p "$backup_dir"; then
    log ERROR "Failed to create backup directory: $backup_dir"
    return 1
  fi

  # Set immediately so cleanup() can report this directory if we crash midway
  USER_MODS_BACKUP="$backup_dir"

  for mod_file in "${MODIFIED_FILES[@]}"; do
    [[ -z "$mod_file" ]] && continue

    src="${WORK_TREE}/${mod_file}"

    # User deleted a tracked file — nothing on disk to copy
    if [[ ! -e "$src" ]]; then
      continue
    fi

    dest="${backup_dir}/${mod_file}"

    if ! mkdir -p "$(dirname "$dest")"; then
      log ERROR "Failed to create directory for: $mod_file"
      return 1
    fi

    if ! cp -a -- "$src" "$dest"; then
      log ERROR "Failed to back up modified file: $mod_file"
      return 1
    fi

    ((file_count++)) || true
  done

  if ((file_count > 0)); then
    log OK "Backed up $file_count modified file(s) to: $backup_dir"
  else
    log INFO "No modified files needed backing up (all were deletions)."
  fi

  return 0
}

# ==============================================================================
# RESTORE USER MODIFICATIONS (File-Based — Replaces Stash Pop)
# ==============================================================================
# After reset --hard, selectively restores user modifications:
#   - Files upstream DIDN'T change: auto-restored via atomic write (rename)
#   - Files upstream DID change: saved to merge directory for manual review
restore_user_modifications() {
  if [[ -z "${USER_MODS_BACKUP:-}" || ! -d "${USER_MODS_BACKUP:-}" ]]; then
    return 0
  fi

  if ((${#MODIFIED_FILES[@]} == 0)); then
    return 0
  fi

  log INFO "Restoring your modifications..."

  # Determine which files upstream changed
  local -A upstream_changed=()
  local uc_file
  local diff_failed=false

  if [[ -n "${PRE_UPDATE_HEAD:-}" ]]; then
    local current_head
    current_head=$("${GIT_CMD[@]}" rev-parse HEAD 2>/dev/null) || current_head=""

    if [[ -n "$current_head" && "$PRE_UPDATE_HEAD" != "$current_head" ]]; then
      local diff_tmpfile
      diff_tmpfile=$(mktemp 2>/dev/null) || diff_tmpfile=""

      if [[ -n "$diff_tmpfile" ]]; then
        if "${GIT_CMD[@]}" diff -z --name-only "$PRE_UPDATE_HEAD" HEAD -- >"$diff_tmpfile" 2>/dev/null; then
          while IFS= read -r -d '' uc_file; do
            [[ -n "$uc_file" ]] && upstream_changed["$uc_file"]=1
          done <"$diff_tmpfile"
        else
          diff_failed=true
        fi
        rm -f "$diff_tmpfile" 2>/dev/null || true
      else
        diff_failed=true
      fi
    fi
  else
    diff_failed=true
  fi

  if [[ "$diff_failed" == true ]]; then
    log WARN "Cannot determine upstream changes. All modified files will go to merge directory."
    for uc_file in "${MODIFIED_FILES[@]}"; do
      [[ -n "$uc_file" ]] && upstream_changed["$uc_file"]=1
    done
  fi

  # Restore or redirect each file
  local merge_dir=""
  local restored_count=0 merge_count=0
  local all_ok=true
  local rest_file backup_src target tmp merge_dest

  for rest_file in "${MODIFIED_FILES[@]}"; do
    [[ -z "$rest_file" ]] && continue

    backup_src="${USER_MODS_BACKUP}/${rest_file}"

    if [[ ! -e "$backup_src" ]]; then
      continue
    fi

    if [[ -v "upstream_changed[$rest_file]" ]]; then
      # Upstream changed this file — save for manual merge
      if [[ -z "$merge_dir" ]]; then
        merge_dir="${BACKUP_BASE_DIR}/needs_merge_${RUN_TIMESTAMP}"
        if ! mkdir -p "$merge_dir"; then
          log ERROR "Failed to create merge directory: $merge_dir"
          all_ok=false
          continue
        fi
      fi

      merge_dest="${merge_dir}/${rest_file}"
      if ! mkdir -p "$(dirname "$merge_dest")"; then
        log ERROR "Failed to create directory for merge file: $rest_file"
        all_ok=false
        continue
      fi

      if cp -a -- "$backup_src" "$merge_dest" 2>/dev/null; then
        ((merge_count++)) || true
        log RAW "  → Upstream changed: $rest_file (your version saved for merge)"
      else
        log ERROR "Failed to copy to merge dir: $rest_file"
        all_ok=false
      fi
    else
      # Upstream didn't change — auto-restore with atomic write
      target="${WORK_TREE}/${rest_file}"
      tmp="${target}.dusky_tmp"

      if ! mkdir -p "$(dirname "$target")" 2>/dev/null; then
        log ERROR "Failed to create directory for restore: $rest_file"
        all_ok=false
        continue
      fi

      if cp -a -- "$backup_src" "$tmp" 2>/dev/null && mv -f -- "$tmp" "$target" 2>/dev/null; then
        ((restored_count++)) || true
        log RAW "  → Restored: $rest_file"
      else
        log ERROR "Failed to restore: $rest_file"
        rm -f "$tmp" 2>/dev/null || true
        all_ok=false
      fi
    fi
  done

  # Summary
  if ((restored_count > 0)); then
    log OK "Auto-restored $restored_count file(s) (upstream hadn't changed them)"
  fi

  if ((merge_count > 0)); then
    log WARN "$merge_count file(s) need manual merge — upstream changed them too"
    log INFO "Your versions saved to:"
    printf '    %s\n' "$merge_dir"
    log INFO "Compare with current configs and merge your changes when ready."
  fi

  if ((restored_count == 0 && merge_count == 0)); then
    log INFO "No modifications needed restoring."
  fi

  # Clean up backup only if ALL files processed successfully
  if [[ "$all_ok" == true ]]; then
    rm -rf "$USER_MODS_BACKUP" 2>/dev/null || true
    USER_MODS_BACKUP=""
  else
    log WARN "Some files could not be processed. Backup preserved at:"
    printf '    %s\n' "$USER_MODS_BACKUP"
  fi

  return 0
}

# ==============================================================================
# INITIAL CLONE — First-run support for new users
# ==============================================================================
initial_clone() {
  log SECTION "First-Time Setup"
  log INFO "Bare repository not found at: $DOTFILES_GIT_DIR"

  local do_clone="y"
  if [[ -t 0 && "$OPT_FORCE" != true ]]; then
    printf '\n'
    read -r -t "$PROMPT_TIMEOUT_LONG" -p "Clone from ${REPO_URL}? [Y/n] " do_clone || do_clone="y"
    do_clone="${do_clone:-y}"
  fi

  if [[ ! "$do_clone" =~ ^[Yy]$ ]]; then
    log INFO "Clone cancelled."
    return 1
  fi

  if [[ "$OPT_DRY_RUN" == true ]]; then
    log INFO "[DRY-RUN] Would clone: $REPO_URL → $DOTFILES_GIT_DIR"
    return 0
  fi

  check_network || return 1

  log INFO "Cloning bare repository..."
  if ! "$GIT_BIN" clone --bare "$REPO_URL" "$DOTFILES_GIT_DIR" >>"$LOG_FILE" 2>&1; then
    log ERROR "Clone failed. Check network and repository URL."
    return 1
  fi

  # Configure the bare repo
  "${GIT_CMD[@]}" config status.showUntrackedFiles no 2>/dev/null || true

  # Checkout files (backup any conflicts)
  log INFO "Checking out files..."
  if ! "${GIT_CMD[@]}" checkout 2>/dev/null; then
    log WARN "Some files already exist. Backing up conflicts..."
    local conflict_backup_dir="${BACKUP_BASE_DIR}/initial_conflicts_${RUN_TIMESTAMP}"
    mkdir -p "$conflict_backup_dir"

    local -a conflict_files=()
    local checkout_err
    checkout_err=$("${GIT_CMD[@]}" checkout 2>&1) || true

    if [[ -n "$checkout_err" ]]; then
      local conflict_file
      while IFS= read -r conflict_file; do
        [[ -z "$conflict_file" ]] && continue
        conflict_files+=("$conflict_file")
      done < <(printf '%s\n' "$checkout_err" | grep $'^\t' | sed 's/^\t//')

      if ((${#conflict_files[@]} > 0)); then
        for conflict_file in "${conflict_files[@]}"; do
          mkdir -p "$conflict_backup_dir/$(dirname "$conflict_file")"
          mv -- "${WORK_TREE}/${conflict_file}" "$conflict_backup_dir/${conflict_file}" 2>/dev/null || true
        done
      fi
    fi

    "${GIT_CMD[@]}" checkout >>"$LOG_FILE" 2>&1 || {
      log ERROR "Checkout failed even after backing up conflicts."
      return 1
    }
    log OK "Conflicts backed up to: $conflict_backup_dir"
  fi

  log OK "Repository cloned and checked out successfully."
  return 0
}

# ==============================================================================
# PULL UPDATES — Sync local repo to upstream
# ==============================================================================
pull_updates() {
  log SECTION "Synchronizing Dotfiles Repository"

  # Handle first-time setup
  if [[ ! -d "$DOTFILES_GIT_DIR" ]]; then
    initial_clone || return 1
    # After initial clone, we're already at latest — no need to fetch again
    log OK "Repository synchronized (initial clone)."
    return 0
  fi

  "${GIT_CMD[@]}" config status.showUntrackedFiles no 2>/dev/null || true

  # Clean any broken state from previous runs
  cleanup_git_state

  # --------------------------------------------------------------------------
  # DETECT LOCAL MODIFICATIONS
  # --------------------------------------------------------------------------
  log INFO "Checking for local modifications..."

  if ! "${GIT_CMD[@]}" diff-index --quiet HEAD -- 2>/dev/null; then
    log WARN "Local modifications detected. These will be preserved."

    PRE_UPDATE_HEAD=$("${GIT_CMD[@]}" rev-parse HEAD 2>/dev/null) || PRE_UPDATE_HEAD=""

    if [[ -z "$PRE_UPDATE_HEAD" ]]; then
      log ERROR "Cannot determine current HEAD. Aborting."
      return 1
    fi

    MODIFIED_FILES=()
    local diff_file
    while IFS= read -r -d '' diff_file; do
      MODIFIED_FILES+=("$diff_file")
    done < <("${GIT_CMD[@]}" diff-index -z --name-only HEAD -- 2>/dev/null | sort -zu)

    if ((${#MODIFIED_FILES[@]} > 0)); then
      log INFO "Found ${#MODIFIED_FILES[@]} modified file(s). Will back up before sync."
    else
      log INFO "No modified files detected (index-only changes)."
    fi
  fi

  # --------------------------------------------------------------------------
  # FIX REMOTE URL
  # --------------------------------------------------------------------------
  local current_url
  current_url=$("${GIT_CMD[@]}" remote get-url origin 2>/dev/null) || current_url=""

  if [[ -z "$current_url" ]]; then
    log WARN "No origin remote. Adding..."
    "${GIT_CMD[@]}" remote add origin "$REPO_URL"
  elif [[ "${current_url%.git}" != "${REPO_URL%.git}" ]]; then
    log WARN "Remote mismatch: $current_url"
    log INFO "Setting to: $REPO_URL"
    "${GIT_CMD[@]}" remote set-url origin "$REPO_URL"
  fi

  # --------------------------------------------------------------------------
  # NETWORK CHECK
  # --------------------------------------------------------------------------
  check_network || return 1

  # --------------------------------------------------------------------------
  # FETCH LATEST (With Exponential Backoff)
  # --------------------------------------------------------------------------
  log INFO "Fetching from upstream..."

  if [[ "$OPT_DRY_RUN" == true ]]; then
    log INFO "[DRY-RUN] Would fetch from origin/${BRANCH}"
  else
    local fetch_success="false"
    local attempt=1
    local wait_time=$FETCH_INITIAL_BACKOFF

    while ((attempt <= FETCH_MAX_ATTEMPTS)); do
      if timeout "${FETCH_TIMEOUT}s" "${GIT_CMD[@]}" fetch origin "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}" >>"$LOG_FILE" 2>&1; then
        fetch_success="true"
        break
      fi

      if ((attempt < FETCH_MAX_ATTEMPTS)); then
        log WARN "Fetch attempt $attempt/$FETCH_MAX_ATTEMPTS failed. Retrying in ${wait_time}s..."
        sleep "$wait_time"
        ((wait_time *= 2))
      fi
      ((attempt++))
    done

    if [[ "$fetch_success" == "false" ]]; then
      log ERROR "Fetch failed after $FETCH_MAX_ATTEMPTS attempts. Check network."
      return 1
    fi

    log OK "Fetch complete."
  fi

  # --------------------------------------------------------------------------
  # HANDLE UNTRACKED FILE COLLISIONS
  # --------------------------------------------------------------------------
  local remote_files untracked_files collision_list
  remote_files=$("${GIT_CMD[@]}" ls-tree -r --name-only "origin/${BRANCH}" 2>/dev/null) || remote_files=""
  untracked_files=$("${GIT_CMD[@]}" ls-files --others --exclude-standard 2>/dev/null) || untracked_files=""

  if [[ -n "$remote_files" && -n "$untracked_files" ]]; then
    collision_list=$(comm -12 <(printf '%s\n' "$remote_files" | sort) \
      <(printf '%s\n' "$untracked_files" | sort) 2>/dev/null) || collision_list=""
  else
    collision_list=""
  fi

  if [[ -n "$collision_list" ]]; then
    local coll_backup_dir="${BACKUP_BASE_DIR}/untracked_collisions_${RUN_TIMESTAMP}"
    log WARN "Untracked collisions found. Backing up..."

    if [[ "$OPT_DRY_RUN" == true ]]; then
      log INFO "[DRY-RUN] Would back up colliding untracked files"
    else
      mkdir -p "$coll_backup_dir"
      local coll_file
      while IFS= read -r coll_file; do
        [[ -z "$coll_file" ]] && continue
        [[ -e "${WORK_TREE}/${coll_file}" ]] || continue
        mkdir -p "$coll_backup_dir/$(dirname "$coll_file")"
        mv -- "${WORK_TREE}/${coll_file}" "$coll_backup_dir/${coll_file}"
        log RAW "  → Backed up: $coll_file"
      done <<<"$collision_list"

      log OK "Collisions backed up to: $coll_backup_dir"
    fi
  fi

  # --------------------------------------------------------------------------
  # SYNC STRATEGY
  # --------------------------------------------------------------------------
  log INFO "Checking sync status..."

  local local_head remote_head base_commit
  local_head=$("${GIT_CMD[@]}" rev-parse HEAD 2>/dev/null) || local_head=""
  remote_head=$("${GIT_CMD[@]}" rev-parse "origin/${BRANCH}" 2>/dev/null) || remote_head=""

  if [[ -z "$local_head" || -z "$remote_head" ]]; then
    if [[ "$OPT_DRY_RUN" == true ]]; then
      log WARN "[DRY-RUN] No cached remote refs found. Cannot preview sync status."
      log INFO "[DRY-RUN] Run without --dry-run to perform an actual fetch first."
      log OK "Repository sync preview complete (limited — no remote data)."
      return 0
    fi
    log ERROR "Cannot determine HEAD commits"
    log ERROR "local_head='$local_head' remote_head='$remote_head'"
    return 1
  fi

  if [[ "$local_head" == "$remote_head" ]]; then
    log OK "Already up to date."
  else
    # Show preview of what's coming
    show_update_preview "$local_head" "$remote_head" || true

    base_commit=$("${GIT_CMD[@]}" merge-base "$local_head" "$remote_head" 2>/dev/null) || base_commit=""

    if [[ "$base_commit" == "$local_head" ]]; then
      log INFO "Fast-forwarding to upstream..."

      if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "[DRY-RUN] Would reset --hard to origin/${BRANCH}"
      else
        if ((${#MODIFIED_FILES[@]} > 0)); then
          if ! backup_user_modifications; then
            log ERROR "Backup failed. Aborting update to protect your files."
            return 1
          fi
        fi
        if "${GIT_CMD[@]}" reset --hard "origin/${BRANCH}" >>"$LOG_FILE" 2>&1; then
          log OK "Updated to latest."
          restore_user_modifications
        else
          log ERROR "Reset failed"
          return 1
        fi
      fi
    else
      log WARN "Local history diverged from upstream."

      if [[ "$OPT_DRY_RUN" == true ]]; then
        log INFO "[DRY-RUN] History diverged. Default action would be: reset to upstream (option 2)"
      else
        printf '\n'
        printf '%s[DIVERGED HISTORY]%s Choose sync method:\n' "$CLR_YLW" "$CLR_RST"
        printf '  1) Abort (keep current state)\n'
        printf '  %s2) Reset to upstream [RECOMMENDED]%s\n' "$CLR_GRN" "$CLR_RST"
        printf '     Your uncommitted tweaks will be backed up and auto-restored where safe.\n'
        printf '  3) Attempt rebase (may fail)\n'
        printf '\n'

        local sync_choice
        if [[ -t 0 ]]; then
          read -r -t "$PROMPT_TIMEOUT_LONG" -p "Choice [1-3] (default: 2): " sync_choice 2>/dev/null || sync_choice="2"
        else
          sync_choice="2"
        fi
        sync_choice="${sync_choice:-2}"

        case "$sync_choice" in
        1)
          log INFO "Aborted."
          return 1
          ;;
        2)
          backup_tracked_files || log WARN "Backup failed, but continuing..."
          if ((${#MODIFIED_FILES[@]} > 0)); then
            if ! backup_user_modifications; then
              log ERROR "Backup failed. Aborting update to protect your files."
              return 1
            fi
          fi
          log INFO "Resetting to upstream..."
          if "${GIT_CMD[@]}" reset --hard "origin/${BRANCH}" >>"$LOG_FILE" 2>&1; then
            log OK "Reset complete."
            restore_user_modifications
          else
            log ERROR "Reset failed"
            return 1
          fi
          ;;
        3)
          backup_tracked_files || log WARN "Backup failed, but continuing..."
          if ((${#MODIFIED_FILES[@]} > 0)); then
            if ! backup_user_modifications; then
              log ERROR "Backup failed. Aborting update to protect your files."
              return 1
            fi
          fi
          # Clean working tree so rebase can proceed
          "${GIT_CMD[@]}" reset --hard HEAD >>"$LOG_FILE" 2>&1 || true
          log INFO "Attempting rebase..."
          local rebase_output rebase_rc=0
          rebase_output=$("${GIT_CMD[@]}" rebase "origin/${BRANCH}" 2>&1) || rebase_rc=$?
          printf '%s\n' "$rebase_output" >>"$LOG_FILE"

          if ((rebase_rc != 0)); then
            log ERROR "Rebase failed."
            log INFO "Aborting and resetting..."
            "${GIT_CMD[@]}" rebase --abort >>"$LOG_FILE" 2>&1 || true

            if "${GIT_CMD[@]}" reset --hard "origin/${BRANCH}" >>"$LOG_FILE" 2>&1; then
              log OK "Fallback reset complete."
              restore_user_modifications
            else
              log ERROR "Reset also failed."
              return 1
            fi
          else
            log OK "Rebase successful."
            restore_user_modifications
          fi
          ;;
        *)
          log INFO "Invalid. Aborting."
          return 1
          ;;
        esac
      fi
    fi
  fi

  log OK "Repository synchronized."
  return 0
}

# ==============================================================================
# SCRIPT EXECUTION
# ==============================================================================
run_script() {
  (($# >= 2)) || {
    log ERROR "run_script: need mode and script"
    return 1
  }

  local -r mode="$1" script="$2"
  shift 2
  local -a args=("$@")
  local script_path

  # Check for custom path override
  if [[ -v "CUSTOM_SCRIPT_PATHS[$script]" && -n "${CUSTOM_SCRIPT_PATHS[$script]}" ]]; then
    script_path="${HOME}/${CUSTOM_SCRIPT_PATHS[$script]}"
  else
    script_path="${SCRIPT_DIR}/${script}"
  fi

  [[ -f "$script_path" ]] || {
    log WARN "Not found: $script"
    return 0
  }
  [[ -r "$script_path" ]] || {
    log WARN "Not readable: $script"
    return 0
  }

  if [[ "$OPT_DRY_RUN" == true ]]; then
    printf '%s→%s [DRY-RUN] %s %s (%s-mode)\n' "$CLR_BLU" "$CLR_RST" "$script" "${args[*]:-}" "$mode"
    return 0
  fi

  # Print script name before execution
  printf '%s→%s %s %s\n' "$CLR_BLU" "$CLR_RST" "$script" "${args[*]:-}"

  local rc=0
  case "$mode" in
  S) sudo "$BASH_BIN" "$script_path" "${args[@]}" || rc=$? ;;
  U) "$BASH_BIN" "$script_path" "${args[@]}" || rc=$? ;;
  *)
    log WARN "Unknown mode: $mode"
    return 0
    ;;
  esac

  if ((rc != 0)); then
    log ERROR "$script failed (exit $rc)"
    FAILED_SCRIPTS+=("$script")
    if [[ "$OPT_STOP_ON_FAIL" == true ]]; then
      log ERROR "Stopping due to --stop-on-fail"
      return 1
    fi
  fi

  return 0
}

# ==============================================================================
# MAIN
# ==============================================================================
main() {
  check_dependencies

  # --------------------------------------------------------------------------
  # CONFIRMATION PROMPT
  # --------------------------------------------------------------------------
  if [[ -t 0 && "$OPT_FORCE" != true && "$OPT_POST_SELF_UPDATE" != true ]]; then
    printf '\n%sNote:%s Avoid interrupting the update while it'\''s running.\n' "${CLR_YLW}" "${CLR_RST}"
    printf 'Interruptions during git operations can leave the repository in a broken state.\n\n'

    local start_confirm
    read -r -p "Start the update? [y/N] " start_confirm
    if [[ ! "$start_confirm" =~ ^[Yy]$ ]]; then
      printf 'Update cancelled.\n'
      exit 0
    fi
  fi

  setup_logging
  auto_prune

  if [[ "$OPT_DRY_RUN" == true ]]; then
    log INFO "Running in DRY-RUN mode — no changes will be made"
  fi

  # Exclusive lock
  if ! : >"$LOCK_FILE" 2>/dev/null; then
    printf 'Error: Cannot create lock file: %s\n' "$LOCK_FILE" >&2
    exit 1
  fi
  exec 9>"$LOCK_FILE"
  flock -n 9 || {
    log ERROR "Another instance running"
    exit 1
  }

  # Determine if sudo is needed (only if we'll run scripts)
  if [[ "$OPT_SYNC_ONLY" != true ]]; then
    scan_needs_sudo
  fi

  # Self-update check hash (skip if post-self-update)
  local self_hash_before=""
  if [[ "$OPT_POST_SELF_UPDATE" != true ]]; then
    [[ -r "$SELF_PATH" ]] && self_hash_before=$(sha256sum "$SELF_PATH" 2>/dev/null | cut -d' ' -f1)
  fi

  # Acquire sudo only if needed
  if [[ "$OPT_NEEDS_SUDO" == true && "$OPT_DRY_RUN" != true ]]; then
    init_sudo
  fi

  # --------------------------------------------------------------------------
  # SYNC PHASE
  # --------------------------------------------------------------------------
  if [[ "$OPT_SKIP_SYNC" != true && "$OPT_POST_SELF_UPDATE" != true ]]; then
    if ! pull_updates; then
      log WARN "Sync failed."
      if [[ "$OPT_SYNC_ONLY" == true ]]; then
        exit 1
      fi
      local cont=""
      if [[ -t 0 ]]; then
        read -r -t "$PROMPT_TIMEOUT_SHORT" -p "Continue with local scripts? [y/N] " cont || cont="n"
      else
        cont="n"
      fi
      [[ "$cont" =~ ^[Yy]$ ]] || exit 1
    else
      # Self-update re-exec (only if we actually synced)
      if [[ -n "$self_hash_before" && -r "$SELF_PATH" ]]; then
        local self_hash_after
        self_hash_after=$(sha256sum "$SELF_PATH" 2>/dev/null | cut -d' ' -f1) || self_hash_after=""
        if [[ -n "$self_hash_after" && "$self_hash_before" != "$self_hash_after" ]]; then
          log SECTION "Self-Update Detected"
          log OK "Reloading with updated script..."
          exec 9>&-
          rm -f "$LOCK_FILE"
          USER_MODS_BACKUP=""
          # Pass --post-self-update to skip re-syncing, plus original flags
          local -a reexec_args=("--post-self-update")
          [[ "$OPT_DRY_RUN" == true ]] && reexec_args+=("--dry-run")
          [[ "$OPT_FORCE" == true ]] && reexec_args+=("--force")
          [[ "$OPT_SKIP_SYNC" == true ]] && reexec_args+=("--skip-sync")
          [[ "$OPT_SYNC_ONLY" == true ]] && reexec_args+=("--sync-only")
          [[ "$OPT_STOP_ON_FAIL" == true ]] && reexec_args+=("--stop-on-fail")
          exec "$SELF_PATH" "${reexec_args[@]}"
        fi
      fi
    fi
  fi

  # --------------------------------------------------------------------------
  # SCRIPT EXECUTION PHASE
  # --------------------------------------------------------------------------
  if [[ "$OPT_SYNC_ONLY" == true ]]; then
    log OK "Sync-only mode — skipping script execution."
  else
    if [[ ! -d "$SCRIPT_DIR" ]]; then
      if [[ "$OPT_DRY_RUN" == true ]]; then
        log WARN "[DRY-RUN] Script directory does not exist yet: $SCRIPT_DIR"
      else
        log ERROR "Script dir missing: $SCRIPT_DIR"
        exit 1
      fi
    fi

    # Acquire sudo now if needed and not already acquired
    if [[ "$OPT_NEEDS_SUDO" == true && -z "$SUDO_PID" && "$OPT_DRY_RUN" != true ]]; then
      init_sudo
    fi

    log SECTION "Executing Update Sequence"

    # Count total active scripts for progress display
    local total_scripts=0
    local entry
    for entry in "${UPDATE_SEQUENCE[@]}"; do
      [[ "$entry" =~ ^[[:space:]]*# ]] && continue
      [[ -z "${entry//[[:space:]]/}" ]] && continue
      ((total_scripts++)) || true
    done

    local current_script=0
    local mode script_part script
    local -a parts args

    for entry in "${UPDATE_SEQUENCE[@]}"; do
      [[ "$entry" =~ ^[[:space:]]*# ]] && continue
      [[ -z "${entry//[[:space:]]/}" ]] && continue

      ((current_script++)) || true

      mode=$(trim "${entry%%|*}")
      script_part=$(trim "${entry#*|}")
      read -ra parts <<<"$script_part"
      script="${parts[0]:-}"
      args=("${parts[@]:1}")

      [[ -n "$script" ]] || {
        log WARN "Malformed: $entry"
        continue
      }

      printf '%s[%d/%d]%s ' "$CLR_CYN" "$current_script" "$total_scripts" "$CLR_RST"
      if ! run_script "$mode" "$script" "${args[@]}"; then
        if [[ "$OPT_STOP_ON_FAIL" == true ]]; then
          break
        fi
      fi
    done
  fi

  # --------------------------------------------------------------------------
  # FINAL SUMMARY
  # --------------------------------------------------------------------------
  printf '\n'
  log SECTION "Summary"

  if [[ "$OPT_DRY_RUN" == true ]]; then
    log INFO "Dry run complete — no changes were made."
  fi

  if ((${#FAILED_SCRIPTS[@]} > 0)); then
    log WARN "${#FAILED_SCRIPTS[@]} script(s) failed:"
    local fs
    for fs in "${FAILED_SCRIPTS[@]}"; do
      printf '    • %s\n' "$fs"
    done
  else
    log OK "All scripts completed successfully."
  fi

  log INFO "Log saved to: $LOG_FILE"

  # Exit with failure if any scripts failed
  if ((${#FAILED_SCRIPTS[@]} > 0)); then
    exit 1
  fi
}

main
