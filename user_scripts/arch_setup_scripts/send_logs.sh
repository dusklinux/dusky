#!/usr/bin/env bash
# ==============================================================================
#  ARCH DOTFILES LOG SUBMITTER (ELITE ARCHITECT EDITION v2.3)
#  - Zero-fork Timestamping (Bash 5+ Native)
#  - Optimized Pacman Dependency Checking
#  - Non-Mutating GitDelta Bare Repo Integration
#  - Capped Systemd Journal & Ring Buffer Extraction
#  - Transient Artifact Isolation (Keeps ~/Documents/logs pristine)
#  - Bulletproof TTY Color Handling & Safe I/O Streaming
#  - Multi-Service Upload Fallback (0x0.st → litterbox → local)
#  - ZERO-CLICK AUTO-UPLOAD ENABLED
# ==============================================================================

# 1. Strict Safety Settings & Parser Directives (Bash 5.3+ optimized)
set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true
shopt -s extglob

# --- CONFIGURATION ---
readonly LOG_SOURCE="${HOME:?HOME is not set}/Documents/logs"
readonly UPLOAD_PRIMARY="https://0x0.st"
readonly UPLOAD_SECONDARY="https://litterbox.catbox.moe/resources/internals/api.php"
readonly GIT_DIR="$HOME/dusky"
readonly WORK_TREE="$HOME"
readonly MAX_UPLOAD_BYTES=536870912  # 512 MiB

# Runtime variables
TEMP_DIR=""
STAGING_DIR=""
ARCHIVE_FILE=""

# --- COLORS ---
RED="" GREEN="" YELLOW="" BLUE="" BOLD="" RESET=""

if [[ -t 1 && -t 2 && -v TERM && "$TERM" != "dumb" ]] && command -v tput &>/dev/null; then
    RED=$(tput setaf 1 2>/dev/null) || true
    GREEN=$(tput setaf 2 2>/dev/null) || true
    YELLOW=$(tput setaf 3 2>/dev/null) || true
    BLUE=$(tput setaf 4 2>/dev/null) || true
    BOLD=$(tput bold 2>/dev/null) || true
    RESET=$(tput sgr0 2>/dev/null) || true
fi

# --- UTILITIES ---
log() {
    printf '%s[%s]%s %s\n' "$BLUE" "${1:-INFO}" "$RESET" "${2:-}" >&2
}

die() {
    printf '%sERROR: %s%s\n' "$RED" "${1:-Unknown error}" "$RESET" >&2
    exit 1
}

cleanup() {
    if [[ -v TEMP_DIR && -d "$TEMP_DIR" ]]; then
        rm -rf -- "$TEMP_DIR"
    fi
}
trap cleanup EXIT

# --- PRE-FLIGHT & SETUP ---
initialize_environment() {
    mkdir -p -- "$LOG_SOURCE" || die "Cannot create log directory: $LOG_SOURCE"
    if [[ ! -w "$LOG_SOURCE" ]]; then
        die "Log directory is not writable: $LOG_SOURCE"
    fi

    # Initialize transient staging area FIRST so reports don't pollute $LOG_SOURCE
    TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/dotfiles_debug.XXXXXX") || die "Cannot create temp dir"
    STAGING_DIR="${TEMP_DIR}/logs"
    ARCHIVE_FILE="${TEMP_DIR}/debug_logs.tar.gz"
    mkdir -p -- "$STAGING_DIR"
}

check_and_install_deps() {
    local -a deps=("curl" "wl-clipboard" "pciutils" "git" "inxi")
    local missing_deps
    
    # Allow stderr to pass through so genuine pacman db lock errors aren't masked
    missing_deps=$(pacman -T "${deps[@]}") || true

    if [[ -n "$missing_deps" ]]; then
        local -a to_install
        mapfile -t to_install <<< "$missing_deps"
        log "WARN" "Installing missing dependencies: ${to_install[*]}"
        sudo pacman -S --needed --noconfirm "${to_install[@]}" || die "Failed to install dependencies."
    fi
}

# --- GITDELTA EXTRACTION ---
capture_gitdelta() {
    log "INFO" "Capturing GitDelta diff from bare repo..."

    local timestamp diff_output
    printf -v timestamp '%(%Y-%m-%d_%H-%M-%S)T' -1
    local delta_file="${STAGING_DIR}/gitdelta_${timestamp}.log"
    local -a git_cmd=("git" "--git-dir=$GIT_DIR" "--work-tree=$WORK_TREE")

    if [[ ! -d "$GIT_DIR" ]]; then
        log "WARNING" "Bare repo not found at $GIT_DIR. Skipping gitdelta."
        return 0
    fi

    # Zshrc Alignment: Update the index FIRST so newly listed files are tracked
    local pathspec_file="${WORK_TREE}/.git_dusky_list"
    if [[ -f "$pathspec_file" ]]; then
        (cd "$WORK_TREE" && "${git_cmd[@]}" add --pathspec-from-file=.git_dusky_list 2>/dev/null) || true
    else
        log "WARNING" "Pathspec file not found: $pathspec_file — skipping git add"
    fi

    # Diff HEAD (matches your 'gitdelta' alias: diffs index + working tree against last commit)
    diff_output=$("${git_cmd[@]}" diff --color=never HEAD 2>/dev/null || true)
    
    if [[ -n "$diff_output" ]]; then
        printf '%s\n' "$diff_output" > "$delta_file"
    else
        log "INFO" "No working tree changes detected. Skipping delta file creation."
    fi
}

# --- REPORT GENERATOR ---
generate_system_report() {
    log "INFO" "Generating comprehensive system, kernel, and environment report..."

    local report_file="${STAGING_DIR}/000_system_hardware_report.txt"
    local current_time
    printf -v current_time '%(%Y-%m-%d %H:%M:%S %Z)T' -1

    {
        printf '========================================================\n'
        printf '  HYPRLAND/ARCH DEBUG REPORT: %s\n' "$current_time"
        printf '========================================================\n\n'

        printf '[KERNEL & DISTRO]\n'
        uname -a 2>/dev/null || true
        grep -E '^(PRETTY_NAME|ID|BUILD_ID)=' /etc/os-release 2>/dev/null || true

        printf '\n[HARDWARE TOPOLOGY (inxi)]\n'
        inxi -Farz 2>/dev/null || printf 'inxi failed or not found.\n'

        if command -v hyprctl &>/dev/null; then
            printf '\n[HYPRLAND STATUS]\n'
            hyprctl version 2>/dev/null | head -n1 || true
        fi

        printf '\n[WAYLAND ENVIRONMENT]\n'
        env | grep -E '^(WAYLAND_DISPLAY|DISPLAY|XDG_CURRENT_DESKTOP|XDG_SESSION_TYPE|QT_QPA_PLATFORM|GBM_BACKEND|LIBVA_DRIVER_NAME|__GLX_VENDOR_LIBRARY_NAME)=' || true

        printf '\n[JOURNALCTL - CURRENT BOOT (WARN+)]\n'
        # Capped to 10000 lines to prevent archive ballooning
        journalctl -b -p 4 --no-pager -n 10000 2>/dev/null || printf 'Requires elevated privileges.\n'

        printf '\n[DMESG]\n'
        local dmesg_out
        if dmesg_out=$(dmesg --color=never 2>/dev/null); then
            printf '%s\n' "$dmesg_out" | tail -n 10000
        else
            printf 'dmesg restricted. Attempting via sudo (non-interactive)...\n'
            sudo -n dmesg --color=never 2>/dev/null | tail -n 10000 || printf 'Failed to read dmesg.\n'
        fi

    } > "$report_file" || die "Cannot write report to disk"
}

# --- PAYLOAD ENGINE ---
prepare_payload() {
    log "PROCESS" "Staging persistent logs from $LOG_SOURCE..."

    shopt -s dotglob nullglob
    local -a log_files=("$LOG_SOURCE"/!(debug_logs_*.tar.gz))
    shopt -u dotglob nullglob

    if (( ${#log_files[@]} > 0 )); then
        cp -r -- "${log_files[@]}" "$STAGING_DIR/" || die "Failed to copy logs to staging"
    fi

    log "PACK" "Compressing archive..."
    tar -czf "$ARCHIVE_FILE" -C "$TEMP_DIR" logs || die "Compression failed"

    local file_bytes
    file_bytes=$(stat -c '%s' "$ARCHIVE_FILE") || die "Cannot stat archive"
    if (( file_bytes > MAX_UPLOAD_BYTES )); then
        log "WARNING" "Archive is $(( file_bytes / 1048576 )) MiB — exceeds upload limit."
    fi
}

# --- UPLOAD ENGINE ---
upload_file() {
    local file="$1"
    local file_bytes response="" url="" attempt

    file_bytes=$(stat -c '%s' "$file") || die "Failed to stat file for upload"

    if (( file_bytes > MAX_UPLOAD_BYTES )); then
        save_local_fallback "$file"
        return 1
    fi

    log "UPLOAD" "Trying 0x0.st..."
    for attempt in 1 2; do
        # Removed 2>&1 to prevent curl stderr warnings from poisoning the $url variable
        if response=$(curl -sS --fail --connect-timeout 30 --max-time 120 -F "file=@${file}" "$UPLOAD_PRIMARY"); then
            read -r url <<< "$response"
            if [[ "$url" == http* ]]; then
                printf '%s' "$url"
                return 0
            fi
        fi
        if (( attempt < 2 )); then sleep 2; fi
    done

    log "UPLOAD" "Trying litterbox.catbox.moe..."
    for attempt in 1 2; do
        if response=$(curl -sS --fail --connect-timeout 30 --max-time 120 -F "reqtype=fileupload" -F "time=72h" -F "fileToUpload=@${file}" "$UPLOAD_SECONDARY"); then
            read -r url <<< "$response"
            if [[ "$url" == http* ]]; then
                printf '%s' "$url"
                return 0
            fi
        fi
        if (( attempt < 2 )); then sleep 2; fi
    done

    save_local_fallback "$file"
    return 1
}

save_local_fallback() {
    local file="$1"
    local timestamp
    printf -v timestamp '%(%Y-%m-%d_%H-%M-%S)T' -1
    local fallback_path="${LOG_SOURCE}/debug_logs_${timestamp}.tar.gz"

    cp -- "$file" "$fallback_path" || die "Failed to save fallback."

    printf '\n%s======================================================%s\n' "$YELLOW" "$RESET" >&2
    printf ' %sUPLOAD FAILED — Archive saved locally%s\n' "$BOLD" "$RESET" >&2
    printf ' Path: %s%s%s\n' "$BLUE" "$fallback_path" "$RESET" >&2
    printf '%s======================================================%s\n' "$YELLOW" "$RESET" >&2
}

# --- MAIN ---
main() {
    initialize_environment
    check_and_install_deps
    capture_gitdelta
    generate_system_report
    prepare_payload

    local file_size
    file_size=$(du -h "$ARCHIVE_FILE" | cut -f1)

    printf '\n%s--- PAYLOAD READY & UPLOADING ---%s\n' "$YELLOW" "$RESET"
    printf 'File:    %s\n' "$ARCHIVE_FILE"
    printf 'Size:    %s\n' "$file_size"
    printf '%s---------------------%s\n' "$YELLOW" "$RESET"

    local url clip_msg=""
    if url=$(upload_file "$ARCHIVE_FILE"); then
        if [[ -v WAYLAND_DISPLAY ]] && command -v wl-copy &>/dev/null; then
            printf '%s' "$url" | wl-copy 2>/dev/null && clip_msg=" (Copied to clipboard)"
        fi

        printf '\n%s======================================================%s\n' "$GREEN" "$RESET"
        printf ' %sSUCCESS!%s%s\n' "$BOLD" "$RESET" "$clip_msg"
        printf ' URL: %s%s%s%s\n' "$BLUE" "$BOLD" "$url" "$RESET"
        printf '%s======================================================%s\n' "$GREEN" "$RESET"
    fi
}

main "$@"
