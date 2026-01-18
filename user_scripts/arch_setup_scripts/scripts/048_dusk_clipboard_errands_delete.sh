#!/usr/bin/env bash
# ==============================================================================
#  MODULE: Persistence Manager (Backup/Restore/Delete)
#  CONTEXT: Hyprland / UWSM / Arch Linux
#  DESCRIPTION: Manages backup, restoration, and cleanup of Clipboard (Cliphist) 
#               and Errands data.
# ==============================================================================

# 1. Strict Error Handling
set -euo pipefail

# ==============================================================================
#  CONSTANTS & CONFIGURATION
# ==============================================================================

readonly BACKUP_DIR="${HOME}/.local/share/rofi_cliphist_and_errands_backup"

# Use XDG_RUNTIME_DIR for a secure, per-user lock file (tmpfs backed)
# Fallback to /tmp is handled safely
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/persistence_manager_${UID}.lock"

# Format: "Path_to_Source:Backup_Subfolder_Name"
readonly TARGETS=(
    "${HOME}/.local/share/rofi-cliphist:rofi-cliphist"
    "${HOME}/.local/share/errands:errands"
)

# Systemd services that must be paused to prevent DB corruption
readonly SERVICES=("cliphist.service" "wl-paste.service")

# GUI Processes to kill (Non-systemd apps that hold file locks)
readonly APPS_TO_KILL=("errands")
readonly KILL_TIMEOUT=50 # 50 * 0.1s = 5 seconds

# ==============================================================================
#  OUTPUT FORMATTING
# ==============================================================================

if [[ -t 2 ]]; then
    readonly RED=$'\033[0;31m'
    readonly GREEN=$'\033[0;32m'
    readonly YELLOW=$'\033[1;33m'
    readonly BLUE=$'\033[0;34m'
    readonly BOLD=$'\033[1m'
    readonly RESET=$'\033[0m'
else
    # Define as empty strings to prevent unbound variable errors with set -u
    readonly RED='' GREEN='' YELLOW='' BLUE='' BOLD='' RESET=''
fi

# Use ${1-} to prevent "unbound variable" errors if called without arguments
log_info() { printf '%s[INFO]%s  %s\n' "$BLUE" "$RESET" "${1-}" >&2; }
log_ok()   { printf '%s[OK]%s    %s\n' "$GREEN" "$RESET" "${1-}" >&2; }
log_warn() { printf '%s[WARN]%s  %s\n' "$YELLOW" "$RESET" "${1-}" >&2; }
log_err()  { printf '%s[ERR]%s   %s\n' "$RED" "$RESET" "${1-}" >&2; }

# ==============================================================================
#  GUARDS & LOCKING
# ==============================================================================

if [[ $EUID -eq 0 ]]; then
    log_err "This script must be run as a normal user, not root."
    exit 1
fi

# Dependency check: Ensure critical tools exist before proceeding
for cmd in flock pgrep pkill systemctl; do
    if ! command -v "$cmd" &>/dev/null; then
        log_err "Required command not found: $cmd"
        exit 1
    fi
done

# Acquire exclusive lock to prevent concurrent execution
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log_err "Another instance of this script is already running."
    exit 1
fi

# ==============================================================================
#  SERVICE & PROCESS MANAGEMENT
# ==============================================================================

# State tracking to prevent restarting services that weren't touched
SERVICES_STOPPED=false

stop_services() {
    log_info "Preparing environment for data migration..."

    # A. Kill GUI Apps (Safe > Wait > Force)
    local app waited
    for app in "${APPS_TO_KILL[@]}"; do
        if pgrep -x "$app" &>/dev/null; then
            log_info "Closing active process: $app"
            pkill -x "$app" || true

            # Wait loop with timeout
            waited=0
            while pgrep -x "$app" &>/dev/null; do
                if ((waited >= KILL_TIMEOUT)); then
                    log_warn "Process $app is unresponsive. Forcing exit (SIGKILL)..."
                    pkill -9 -x "$app" 2>/dev/null || true
                    sleep 0.2
                    break
                fi
                sleep 0.1
                # FIXED: ((waited++)) returns 0 on first run, crashing set -e.
                # Use +=1 to ensure the expression evaluates to >0 (True).
                ((waited+=1))
            done
        fi
    done

    # B. Stop Systemd Services
    local service
    for service in "${SERVICES[@]}"; do
        if systemctl --user is-active --quiet "$service" 2>/dev/null; then
            log_info "Stopping service: $service"
            systemctl --user stop "$service" || log_warn "Failed to stop $service"
        fi
    done

    SERVICES_STOPPED=true
}

restart_services() {
    [[ $SERVICES_STOPPED == true ]] || return 0

    # Only restart if in a Wayland session
    if [[ -n ${WAYLAND_DISPLAY-} ]]; then
        log_info "Restarting background services..."
        local service
        for service in "${SERVICES[@]}"; do
            systemctl --user start "$service" 2>/dev/null || true
        done
    fi
}

cleanup() {
    local exit_code=$?
    if ((exit_code != 0)); then
        log_err "Script failed with exit code $exit_code"
    fi
    restart_services
    rm -f "$LOCK_FILE"
}
trap cleanup EXIT

# ==============================================================================
#  CORE FUNCTIONS
# ==============================================================================

do_backup() {
    log_info "Starting BACKUP operation (Copy)..."

    mkdir -p "$BACKUP_DIR"
    stop_services

    local backup_count=0
    local source_path backup_name dest_path

    for item in "${TARGETS[@]}"; do
        source_path=${item%%:*}
        backup_name=${item##*:}
        dest_path="${BACKUP_DIR}/${backup_name}"

        if [[ -e $source_path ]]; then
            # 1. Clean old backup artifact to ensure fresh copy
            if [[ -e $dest_path ]]; then
                log_info "Removing old backup: $backup_name"
                rm -rf "$dest_path"
            fi
            
            # 2. Copy source to backup (Archive mode)
            # cp -a ensures permissions/times are preserved
            cp -a "$source_path" "$dest_path"
            log_ok "Backed up ${BOLD}${source_path##*/}${RESET}"
            
            # FIXED: Post-increment on 0 triggers set -e
            ((backup_count+=1))
        else
            log_warn "Source not found, skipping: $source_path"
        fi
    done

    # Flush to disk
    sync

    if ((backup_count == 0)); then
        log_warn "No files were found to backup."
    else
        log_ok "Backup complete. $backup_count item(s) copied to: $BACKUP_DIR"
    fi
}

do_restore() {
    log_info "Starting RESTORE operation (Copy)..."

    if [[ ! -d $BACKUP_DIR ]]; then
        log_err "No backup directory found at: $BACKUP_DIR"
        exit 1
    fi

    stop_services

    local restored_count=0
    local source_path backup_name backup_source parent_dir

    for item in "${TARGETS[@]}"; do
        source_path=${item%%:*}
        backup_name=${item##*:}
        backup_source="${BACKUP_DIR}/${backup_name}"
        
        # Fast parameter expansion for dirname
        parent_dir=${source_path%/*}

        if [[ -e $backup_source ]]; then
            # Guard against empty string if source is at root
            [[ -n $parent_dir ]] && mkdir -p "$parent_dir"

            # 1. If current live data exists, nuke it to replace with backup
            if [[ -e $source_path ]]; then
                log_warn "Overwriting existing data at: $source_path"
                rm -rf "$source_path"
            fi

            # 2. Copy backup back to live location
            cp -a "$backup_source" "$source_path"
            log_ok "Restored ${BOLD}${backup_name}${RESET} -> ${source_path}"
            
            # FIXED: Post-increment on 0 triggers set -e
            ((restored_count+=1))
        else
            log_info "Backup artifact not found for: $backup_name"
        fi
    done

    sync

    if ((restored_count == 0)); then
        log_warn "Nothing was restored. Backup folder might be empty or corrupt."
    else
        log_ok "Restore complete. $restored_count item(s) restored."
        log_info "Note: Backups were preserved in $BACKUP_DIR"
    fi
}

do_delete() {
    log_warn "Starting DELETE operation..."
    log_warn "This will PERMANENTLY remove data from BOTH the live system and backups."

    stop_services

    local deleted_count=0
    local source_path backup_name backup_path

    for item in "${TARGETS[@]}"; do
        source_path=${item%%:*}
        backup_name=${item##*:}
        backup_path="${BACKUP_DIR}/${backup_name}"

        # 1. Delete Live Data
        if [[ -e $source_path ]]; then
            rm -rf "$source_path"
            log_ok "Deleted Live: ${source_path}"
            # FIXED: Post-increment on 0 triggers set -e
            ((deleted_count+=1))
        fi

        # 2. Delete Backup Data
        if [[ -e $backup_path ]]; then
            rm -rf "$backup_path"
            log_ok "Deleted Backup: ${backup_path}"
            # FIXED: Post-increment on 0 triggers set -e
            ((deleted_count+=1))
        fi
    done

    sync

    if [[ -d $BACKUP_DIR ]]; then
        rmdir "$BACKUP_DIR" 2>/dev/null || true
    fi

    if ((deleted_count == 0)); then
        log_warn "No files found to delete in either location."
    else
        log_ok "Cleanup complete. $deleted_count items removed."
    fi
}

show_help() {
    cat <<EOF
Usage: ${0##*/} [OPTION]

Manage backup/restore of Cliphist and Errands data.

Options:
    --backup     Copy current data to backup storage
    --restore    Copy stored data back to live system
    --delete     Permanently delete data from BOTH live and backup locations
    --help, -h   Show this help message

Without options, an interactive menu is displayed.
EOF
}

# ==============================================================================
#  MAIN ENTRY POINT
# ==============================================================================

main() {
    local mode=''

    # 1. Argument Parsing
    while (($# > 0)); do
        case $1 in
            --backup)
                [[ -z $mode ]] || { log_err "Conflicting options selected."; exit 1; }
                mode='backup'
                ;;
            --restore)
                [[ -z $mode ]] || { log_err "Conflicting options selected."; exit 1; }
                mode='restore' 
                ;;
            --delete)
                [[ -z $mode ]] || { log_err "Conflicting options selected."; exit 1; }
                mode='delete'
                ;;
            --help|-h) 
                show_help; exit 0 
                ;;
            *)
                log_err "Unknown argument: $1"
                show_help >&2
                exit 1
                ;;
        esac
        shift
    done

    # 2. Interactive Menu (If no args)
    if [[ -z $mode ]]; then
        # Check for TTY
        if [[ ! -t 0 ]]; then
            log_err "No TTY detected. Interactive mode requires a terminal."
            log_err "Use --backup, --restore, or --delete flags."
            exit 1
        fi

        printf '\n%sSelect an operation:%s\n' "$BOLD" "$RESET"
        PS3='> '
        local options=(
            "Backup (Copy current data to storage)" 
            "Restore (Copy stored data back)" 
            "Delete (Permanently wipe ALL data)"
            "Cancel"
        )
        
        select opt in "${options[@]}"; do
            case $opt in
                "Backup (Copy current data to storage)")
                    mode='backup'; break ;;
                "Restore (Copy stored data back)")
                    mode='restore'; break ;;
                "Delete (Permanently wipe ALL data)")
                    mode='delete'; break ;;
                "Cancel")
                    log_info "Operation cancelled."; exit 0 ;;
                *)
                    log_warn "Invalid selection: $REPLY" ;;
            esac
        done

        if [[ -z $mode ]]; then
            printf '\n'
            log_info "Operation cancelled (EOF)."
            exit 0
        fi
    fi

    # 3. Execution
    case $mode in
        backup)  do_backup ;;
        restore) do_restore ;;
        delete)  do_delete ;;
    esac
}

main "$@"
