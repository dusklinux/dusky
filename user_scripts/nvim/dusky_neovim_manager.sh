#!/usr/bin/env bash
# Neovim distribution deployment and state management. Requires Bash 5.3+.
set -euo pipefail

log_info() { printf '[INFO] %s\n' "$*"; }
log_success() { printf '[SUCCESS] %s\n' "$*"; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }

declare -A NVIM_PATHS=()
BACKUP_DIR=""
DUSKY_SRC=""
STAGE=""
CURRENT_BACKUP_PATH=""
DEPLOYING=false
PUBLISHING=false
RESTORED_PARTS=()

exists() { [[ -e "$1" || -L "$1" ]]; }

initialize() {
    local cmd key other path protected
    local -A canonical=()
    local protected_paths=()
    for cmd in git nvim timeout cp mv rm mkdir mktemp realpath flock find sort; do
        command -v "$cmd" >/dev/null || { log_error "Missing dependency: $cmd"; return 1; }
    done
    export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
    export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
    export XDG_STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
    export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
    NVIM_PATHS=(
        [config]="${XDG_CONFIG_HOME}/nvim"
        [data]="${XDG_DATA_HOME}/nvim"
        [state]="${XDG_STATE_HOME}/nvim"
        [cache]="${XDG_CACHE_HOME}/nvim"
    )
    DUSKY_SRC="${XDG_CONFIG_HOME}/dusky_nvim"
    BACKUP_DIR="${XDG_DATA_HOME}/nvim_backups"
    for key in config data state cache; do
        path=${NVIM_PATHS[$key]}
        [[ "$path" == /* ]] || { log_error "XDG paths must be absolute: $path"; return 1; }
        NVIM_PATHS[$key]=$(realpath --canonicalize-missing --no-symlinks -- "$path") || return 1
    done
    for key in config data state cache; do
        canonical[$key]=$(realpath --canonicalize-missing -- "${NVIM_PATHS[$key]}") || return 1
    done
    for protected in "$DUSKY_SRC" "$BACKUP_DIR"; do
        protected=$(realpath --canonicalize-missing -- "$protected") || return 1
        protected_paths+=("$protected")
    done
    for key in config data state cache; do
        path=${canonical[$key]}
        for protected in "${protected_paths[@]}"; do
            if [[ "$protected/" == "$path/"* || "$path/" == "$protected/"* ]]; then
                log_error "Source or backup storage overlaps managed directory: $path"
                return 1
            fi
        done
        for other in config data state cache; do
            [[ "$key" == "$other" ]] && continue
            [[ "$path/" != "${canonical[$other]}/"* ]] || {
                log_error "Managed Neovim directories overlap"; return 1;
            }
        done
    done
    mkdir -p -- "${XDG_STATE_HOME}/dusky" || return 1
    exec 9>"${XDG_STATE_HOME}/dusky/nvim-manager.lock" || return 1
    flock --nonblocking 9 || { log_error "Another Neovim manager operation is running"; return 1; }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
}

safe_rm() {
    local target=$1 key
    for key in config data state cache; do
        if [[ "$target" == "${NVIM_PATHS[$key]}" ]]; then
            rm -rf -- "$target"
            return
        fi
    done
    log_error "Unknown Neovim directory: $target"
    return 1
}

rollback() {
    local key
    for key in "${RESTORED_PARTS[@]}"; do
        safe_rm "${NVIM_PATHS[$key]}" || return 1
    done
    [[ -n "$CURRENT_BACKUP_PATH" ]] || return 0
    for key in config data state cache; do
        if exists "$CURRENT_BACKUP_PATH/$key"; then
            if exists "${NVIM_PATHS[$key]}"; then
                log_error "Rollback cannot overwrite ${NVIM_PATHS[$key]}; backup: $CURRENT_BACKUP_PATH"
                return 1
            fi
            mv -T -- "$CURRENT_BACKUP_PATH/$key" "${NVIM_PATHS[$key]}" || return 1
        fi
    done
}

cleanup() {
    local status=$?
    trap - EXIT
    if $DEPLOYING && (( status != 0 )); then
        # The rename may finish just before a signal runs this trap.
        if $PUBLISHING && [[ -n "$STAGE" ]] && ! exists "$STAGE"; then
            safe_rm "${NVIM_PATHS[config]}" || log_error "Could not remove interrupted replacement"
        fi
        rollback || log_error "Previous state remains at $CURRENT_BACKUP_PATH"
    fi
    if [[ -n "$STAGE" ]] && exists "$STAGE"; then
        rm -rf -- "$STAGE"
    fi
    return "$status"
}

backup_neovim_state() {
    # copy = maintenance backup; move = deployment/reset backup.
    local mode=${1:-copy} key has_state=false
    for key in config data state cache; do
        if exists "${NVIM_PATHS[$key]}"; then has_state=true; break; fi
    done
    if ! $has_state; then
        CURRENT_BACKUP_PATH=""
        log_info "No existing Neovim state to back up"
        return 0
    fi
    mkdir -p -- "$BACKUP_DIR" || return 1
    CURRENT_BACKUP_PATH=$(mktemp -d "$BACKUP_DIR/backup_XXXXXXXXXX") || return 1
    for key in config data state cache; do
        if exists "${NVIM_PATHS[$key]}"; then
            if [[ "$mode" == move ]]; then
                mv -T -- "${NVIM_PATHS[$key]}" "$CURRENT_BACKUP_PATH/$key" || return 1
            else
                cp -a -- "${NVIM_PATHS[$key]}" "$CURRENT_BACKUP_PATH/$key" || return 1
            fi
        fi
    done
    log_success "Backup: $CURRENT_BACKUP_PATH"
}

wipe_neovim_state() {
    local key
    for key in config data state cache; do
        safe_rm "${NVIM_PATHS[$key]}" || return 1
    done
}

reset_neovim_state() {
    local key
    for key in data state cache; do
        safe_rm "${NVIM_PATHS[$key]}" || return 1
    done
    log_success "Data, state and cache cleared; configuration retained"
}

prepare_target() {
    local target=$1 repo
    case "$target" in
        dusky) [[ -f "$DUSKY_SRC/init.lua" ]] || { log_error "Missing source: $DUSKY_SRC/init.lua"; return 1; } ;;
        nvchad) repo=https://github.com/NvChad/starter ;;
        lazyvim) repo=https://github.com/LazyVim/starter ;;
        astronvim) repo=https://github.com/AstroNvim/template ;;
        *) log_error "Invalid target: $target"; return 1 ;;
    esac
    mkdir -p -- "${NVIM_PATHS[config]%/*}" || return 1
    STAGE=$(mktemp -d "${NVIM_PATHS[config]%/*}/.nvim-stage.XXXXXXXXXX") || return 1
    if [[ "$target" == dusky ]]; then
        cp -a -- "$DUSKY_SRC/." "$STAGE/" || return 1
    else
        git clone --depth 1 -- "$repo" "$STAGE" || return 1
        rm -rf -- "$STAGE/.git" || return 1
    fi
}

deploy() {
    local target=$1 mode=${2:-backup}
    prepare_target "$target" || return 1
    DEPLOYING=true
    if [[ "$mode" == backup ]]; then
        backup_neovim_state move || return 1
    else
        wipe_neovim_state || return 1
    fi
    PUBLISHING=true
    mv -T -- "$STAGE" "${NVIM_PATHS[config]}" || return 1
    DEPLOYING=false
    PUBLISHING=false
    STAGE=""
    log_success "Deployed $target"
}

execute_headless_sync() {
    local helper
    helper="$(dirname -- "${BASH_SOURCE[0]}")/reset/02_cli_plugins_download.sh"
    DUSKY_NVIM_LOCK_HELD=1 bash "$helper"
}

restore_neovim_state() {
    local backups=() chosen key target has_state
    [[ -d "$BACKUP_DIR" ]] || { log_error "No backups found"; return 1; }
    mapfile -t backups < <(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name 'backup_*' -printf '%T@ %f\n' | sort -nr)
    (( ${#backups[@]} )) || { log_error "No backups found"; return 1; }
    for key in "${!backups[@]}"; do backups[key]=${backups[key]#* }; done
    PS3='Restore backup: '
    select chosen in "${backups[@]}" Cancel; do
        [[ "$chosen" == Cancel ]] && return 0
        [[ -n "$chosen" ]] || continue
        target="$BACKUP_DIR/$chosen"
        # Copy the selected backup before moving any live directories.
        STAGE=$(mktemp -d "$BACKUP_DIR/.restore.XXXXXXXXXX") || return 1
        cp -a -- "$target/." "$STAGE/" || return 1
        has_state=false
        for key in config data state cache; do
            if exists "$STAGE/$key"; then has_state=true; break; fi
        done
        $has_state || { log_error "Selected backup contains no Neovim state"; return 1; }
        DEPLOYING=true
        backup_neovim_state move || return 1
        for key in config data state cache; do
            if exists "$STAGE/$key"; then
                mkdir -p -- "${NVIM_PATHS[$key]%/*}" || return 1
                RESTORED_PARTS+=("$key")
                cp -a -- "$STAGE/$key" "${NVIM_PATHS[$key]}" || return 1
            fi
        done
        DEPLOYING=false
        log_success "Restored $chosen"
        return 0
    done
    return 1
}

print_help() {
    printf 'Usage: %s [--auto --target dusky|nvchad|lazyvim|astronvim] [--wipe] [--no-sync]\n' "${0##*/}"
    printf 'Default: interactive menu. Automatic deployment backs up existing state.\n'
    printf '  --wipe     Delete existing state instead of backing it up.\n'
    printf '  --no-sync  Deploy offline; retain the copied lockfile without downloading plugins.\n'
}

main() {
    local auto=false target="" mode=backup sync=true choice
    while (( $# )); do
        case "$1" in
            -h|--help) print_help; return 0 ;;
            -a|--auto) auto=true; shift ;;
            -t|--target) [[ -n "${2:-}" && "$2" != -* ]] || { log_error 'Missing target'; return 1; }; target=${2,,}; shift 2 ;;
            --wipe) mode=wipe; shift ;;
            --no-sync) sync=false; shift ;;
            *) log_error "Unknown argument: $1"; return 1 ;;
        esac
    done
    if $auto; then
        case "$target" in dusky|nvchad|lazyvim|astronvim) ;; *) log_error 'Automatic mode requires a valid target'; return 1 ;; esac
    elif [[ -n "$target" || "$mode" == wipe ]]; then
        log_error '--target and --wipe require --auto'; return 1
    fi
    initialize || return 1
    if $auto; then
        deploy "$target" "$mode" || return 1
        if $sync; then execute_headless_sync || return 1; fi
        return 0
    fi
    PS3='Neovim operation: '
    select choice in 'Install NvChad' 'Install LazyVim' 'Install AstroNvim' 'Install Dusky' 'Backup' 'Restore' 'Sync plugins' 'Reset data/state/cache' Quit; do
        case "$choice" in
            'Install NvChad') target=nvchad ;;
            'Install LazyVim') target=lazyvim ;;
            'Install AstroNvim') target=astronvim ;;
            'Install Dusky') target=dusky ;;
            Backup) backup_neovim_state; return ;;
            Restore) restore_neovim_state; return ;;
            'Sync plugins') execute_headless_sync; return ;;
            'Reset data/state/cache') reset_neovim_state; return ;;
            Quit) return 0 ;;
            *) log_error 'Invalid selection'; continue ;;
        esac
        PS3='Existing state: '
        choice=""
        select choice in Backup Wipe Cancel; do
            case "$choice" in
                Backup) mode=backup; break ;;
                Wipe) mode=wipe; break ;;
                Cancel) return 0 ;;
            esac
        done
        [[ "$choice" == Backup || "$choice" == Wipe ]] || return 1
        deploy "$target" "$mode" || return 1
        if $sync; then execute_headless_sync || return 1; fi
        return 0
    done
    return 1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
