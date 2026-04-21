#!/usr/bin/env bash
# ==============================================================================
# pacman_repo_switch.sh
#
# Manages pacman's repository configuration, toggling between:
#   OFFLINE: A local flat repository on installation media (file://)
#   ONLINE:  Standard Arch Linux HTTPS mirrors
#
# Works correctly in BOTH:
#   - Arch Linux ISO live environment and install chroot (already root)
#   - Post-installed Arch Linux system (self-elevates via sudo if needed)
#
# Usage:
#   ./pacman_repo_switch.sh              # Interactive menu
#   ./pacman_repo_switch.sh --online     # Apply online config immediately
#   ./pacman_repo_switch.sh --offline    # Apply offline config immediately
#   ./pacman_repo_switch.sh --help       # Show usage information
#
# ==============================================================================

set -euo pipefail

# ==============================================================================
# SECTION 1 — USER CONFIGURATION
# These are the only values you should need to edit for your environment.
# ==============================================================================

# Dynamically detect if we are inside the chroot (Phase 2) or on the ISO (Phase 1)
# MUST begin with 'file:///' (three slashes) to ensure an absolute path.
if [[ -d "/offline_repo" ]]; then
    OFFLINE_REPO_PATH="file:///offline_repo"
else
    OFFLINE_REPO_PATH="file:///run/archiso/bootmnt/arch/repo"
fi

# The name of the custom repository section used in the offline pacman.conf.
# MUST exactly match the base name of your repository database file (without
# the .db extension).
# Example: if your database file is 'archrepo.db', set this to 'archrepo'.
OFFLINE_REPO_NAME="archrepo"

# Paths to the config files being managed. Change only if non-standard.
PACMAN_CONF="/etc/pacman.conf"
MIRRORLIST_FILE="/etc/pacman.d/mirrorlist"

# Backup suffix. A single backup per file is kept (overwritten on each run).
# This guarantees idempotency — no accumulation of backup files over time.
BACKUP_SUFFIX=".pacman-switch.bak"

# ==============================================================================
# SECTION 2 — SELF-ELEVATION
#
# This block runs at the top level of the script, before any functions.
# If the effective user is not root, we attempt to re-execute this exact script
# under sudo, forwarding all original arguments.
# ==============================================================================

if [[ "${EUID}" -ne 0 ]]; then
    # Sanity check: Prevent exec sudo if piped from stdin or running as string.
    if [[ -z "${BASH_SOURCE[0]:-}" || ! -f "${BASH_SOURCE[0]}" ]]; then
        echo "[ERROR] Cannot self-elevate. Script must be executed from a file, not stdin/pipe." >&2
        echo "[ERROR] Please run this script as root directly." >&2
        exit 1
    fi

    # Resolve the absolute, symlink-resolved path to this script.
    _SELF="$(readlink -f "${BASH_SOURCE[0]}")"

    if command -v sudo &>/dev/null; then
        echo "[INFO]  Root privileges are required."
        echo "[INFO]  Re-launching under sudo — you may be prompted for your password."
        # 'exec' replaces this process. If sudo succeeds, this line never returns.
        exec sudo "${_SELF}" "$@"
        # If exec somehow returns, something went very wrong.
        echo "[ERROR] 'exec sudo' failed unexpectedly." >&2
        exit 1
    else
        echo "[ERROR] Root privileges are required and 'sudo' was not found." >&2
        echo "[ERROR] Please run this script as root directly." >&2
        echo "[ERROR] In the Arch ISO environment, you should already be root." >&2
        exit 1
    fi
fi

# ==============================================================================
# SECTION 3 — TERMINAL COLOR SETUP
# ==============================================================================

if [[ -t 1 ]]; then
    CLR_RED=$(tput setaf 1 2>/dev/null)    || CLR_RED=""
    CLR_GREEN=$(tput setaf 2 2>/dev/null)  || CLR_GREEN=""
    CLR_YELLOW=$(tput setaf 3 2>/dev/null) || CLR_YELLOW=""
    CLR_CYAN=$(tput setaf 6 2>/dev/null)   || CLR_CYAN=""
    CLR_BOLD=$(tput bold 2>/dev/null)      || CLR_BOLD=""
    CLR_RESET=$(tput sgr0 2>/dev/null)     || CLR_RESET=""
else
    CLR_RED=""
    CLR_GREEN=""
    CLR_YELLOW=""
    CLR_CYAN=""
    CLR_BOLD=""
    CLR_RESET=""
fi

# ==============================================================================
# SECTION 4 — LOGGING HELPERS
# ==============================================================================

log_info()  { printf "%s[INFO]%s  %s\n"  "${CLR_GREEN}"  "${CLR_RESET}" "$*";      }
log_warn()  { printf "%s[WARN]%s  %s\n"  "${CLR_YELLOW}" "${CLR_RESET}" "$*";      }
log_error() { printf "%s[ERROR]%s %s\n"  "${CLR_RED}"    "${CLR_RESET}" "$*" >&2;  }
log_step()  { printf "\n%s%s==>%s %s%s%s\n" \
                  "${CLR_BOLD}" "${CLR_CYAN}" "${CLR_RESET}" \
                  "${CLR_BOLD}" "$*"          "${CLR_RESET}";                       }

# ==============================================================================
# SECTION 5 — STARTUP VALIDATION
# ==============================================================================

validate_config() {
    local errors=0

    # --- Validate OFFLINE_REPO_PATH -------------------------------------------
    # Enforce file:/// (three slashes) to guarantee absolute path conversion.
    if [[ "${OFFLINE_REPO_PATH}" != file:///* ]]; then
        log_error "OFFLINE_REPO_PATH must begin with 'file:///' (three slashes)."
        log_error "  Current value : '${OFFLINE_REPO_PATH}'"
        log_error "  Example       : 'file:///run/archiso/bootmnt/arch/repo'"
        errors=$(( errors + 1 ))
    fi

    # --- Validate OFFLINE_REPO_NAME -------------------------------------------
    if [[ -z "${OFFLINE_REPO_NAME}" ]]; then
        log_error "OFFLINE_REPO_NAME must not be empty."
        log_error "  Set it to the base name of your .db file (without extension)."
        errors=$(( errors + 1 ))
    elif [[ "${OFFLINE_REPO_NAME}" =~ [[:space:]/\\] ]]; then
        log_error "OFFLINE_REPO_NAME must not contain spaces, forward slashes, or backslashes."
        log_error "  Current value : '${OFFLINE_REPO_NAME}'"
        errors=$(( errors + 1 ))
    fi

    # --- Validate config file parent directories exist ------------------------
    local pacman_conf_dir mirrorlist_dir
    pacman_conf_dir="$(dirname "${PACMAN_CONF}")"
    mirrorlist_dir="$(dirname "${MIRRORLIST_FILE}")"

    if [[ ! -d "${pacman_conf_dir}" ]]; then
        log_error "Parent directory for PACMAN_CONF does not exist: '${pacman_conf_dir}'"
        errors=$(( errors + 1 ))
    fi

    if [[ ! -d "${mirrorlist_dir}" ]]; then
        log_error "Parent directory for MIRRORLIST_FILE does not exist: '${mirrorlist_dir}'"
        errors=$(( errors + 1 ))
    fi

    # --- Abort if any validation failed ---------------------------------------
    if (( errors > 0 )); then
        log_error "Configuration validation failed with ${errors} error(s). Aborting."
        exit 1
    fi
}

# ==============================================================================
# SECTION 6 — ATOMIC FILE WRITE
# ==============================================================================

write_file_atomically() {
    local dest="${1:?write_file_atomically: a destination path argument is required}"
    local dest_dir
    dest_dir="$(dirname "${dest}")"
    local tmpfile

    if [[ ! -d "${dest_dir}" ]]; then
        log_error "Destination directory does not exist: '${dest_dir}'"
        return 1
    fi

    tmpfile="$(mktemp -p "${dest_dir}" .pacman-switch.XXXXXXXXXX)"
    
    # Safety Check: Guarantee temp file was created before proceeding
    if [[ -z "${tmpfile}" || ! -f "${tmpfile}" ]]; then
        log_error "Failed to create temporary file in '${dest_dir}'."
        return 1
    fi

    chmod 0644 "${tmpfile}"
    chown root:root "${tmpfile}"

    if [[ -f "${dest}" ]]; then
        chmod --reference="${dest}" "${tmpfile}" 2>/dev/null || true
        chown --reference="${dest}" "${tmpfile}" 2>/dev/null || true
    fi

    if ! cat > "${tmpfile}"; then
        rm -f "${tmpfile}"
        log_error "Failed to write content to temporary file: '${tmpfile}'"
        return 1
    fi

    if ! mv "${tmpfile}" "${dest}"; then
        rm -f "${tmpfile}"
        log_error "Failed to rename temp file to destination: '${dest}'"
        return 1
    fi

    return 0
}

# ==============================================================================
# SECTION 7 — BACKUP HELPER
# ==============================================================================

backup_file() {
    local src="${1:?backup_file: a source file path argument is required}"
    local backup="${src}${BACKUP_SUFFIX}"

    if [[ ! -f "${src}" ]]; then
        log_warn "Source file '${src}' not found — skipping backup."
        return 0
    fi

    if [[ -f "${backup}" ]]; then
        log_warn "Overwriting existing backup: '${backup}'"
    fi

    cp --preserve=all "${src}" "${backup}"
    log_info "Backup saved: '${backup}'"
}

# ==============================================================================
# SECTION 8 — NETWORK CONNECTIVITY CHECK
# ==============================================================================

check_network() {
    if command -v curl &>/dev/null; then
        if curl --silent --max-time 5 --head "https://archlinux.org" &>/dev/null; then
            return 0
        fi
    fi

    if command -v wget &>/dev/null; then
        if wget --quiet --spider --timeout=5 "https://archlinux.org" &>/dev/null; then
            return 0
        fi
    fi

    if ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
        return 0
    fi

    return 1
}

# ==============================================================================
# SECTION 9 — SWITCH TO ONLINE
# ==============================================================================

switch_to_online() {
    log_step "Switching to ONLINE Repositories"

    local write_timestamp
    write_timestamp="$(date --utc '+%Y-%m-%d %H:%M:%S UTC')"

    log_info "Backing up existing configuration files..."
    backup_file "${PACMAN_CONF}"
    backup_file "${MIRRORLIST_FILE}"

    log_info "Writing online pacman.conf -> '${PACMAN_CONF}'..."

    write_file_atomically "${PACMAN_CONF}" << 'ONLINE_PACMAN_CONF_EOF'
# ==============================================================================
# /etc/pacman.conf — ONLINE MODE
# ==============================================================================
# Managed by: pacman_repo_switch.sh
# State:      ONLINE — standard Arch Linux HTTPS mirrors
#
# To switch states:
#   sudo pacman_repo_switch.sh --online
#   sudo pacman_repo_switch.sh --offline
# ==============================================================================

[options]
Color
ILoveCandy
VerbosePkgLists
HoldPkg     = pacman glibc
Architecture = auto
CheckSpace
ParallelDownloads = 5
DownloadUser = alpm

SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional

[core]
Include = /etc/pacman.d/mirrorlist

[extra]
Include = /etc/pacman.d/mirrorlist

[multilib]
Include = /etc/pacman.d/mirrorlist
ONLINE_PACMAN_CONF_EOF

    log_info "Online pacman.conf written successfully."
    log_info "Writing online mirrorlist -> '${MIRRORLIST_FILE}'..."

    write_file_atomically "${MIRRORLIST_FILE}" << 'ONLINE_MIRRORLIST_EOF'
################################################################################
# /etc/pacman.d/mirrorlist — ONLINE MODE
################################################################################
# Managed by: pacman_repo_switch.sh
# State:      ONLINE — standard Arch Linux HTTPS mirrors
#
# TO UPDATE: Replace the Server = lines below with your own mirrors,
# or regenerate this file with reflector after booting online:
#   reflector --latest 20 --protocol https --sort rate --save /etc/pacman.d/mirrorlist

Server = https://frankfurt.mirror.pkgbuild.com/$repo/os/$arch
Server = https://johannesburg.mirror.pkgbuild.com/$repo/os/$arch
Server = https://london.mirror.pkgbuild.com/$repo/os/$arch
Server = https://losangeles.mirror.pkgbuild.com/$repo/os/$arch
Server = https://mirror.moson.org/arch/$repo/os/$arch
Server = https://mirror.sunred.org/archlinux/$repo/os/$arch
Server = https://arch.mirror.constant.com/$repo/os/$arch
Server = https://arch.phinau.de/$repo/os/$arch
Server = https://mirror.theo546.fr/archlinux/$repo/os/$arch
Server = https://berlin.mirror.pkgbuild.com/$repo/os/$arch
ONLINE_MIRRORLIST_EOF

    log_info "Online mirrorlist written successfully."
    log_step "Syncing Package Databases"

    if check_network; then
        log_info "Network reachable. Running 'pacman -Syy'..."
        local pacman_exit=0
        pacman -Syy || pacman_exit=$?

        if (( pacman_exit == 0 )); then
            log_info "Package databases synced successfully."
        else
            log_warn "'pacman -Syy' exited with code ${pacman_exit}."
            log_warn "Your configuration files are correctly written."
        fi
    else
        log_warn "Network not reachable — skipping 'pacman -Syy'."
        log_warn "Once connected, run: sudo pacman -Syy"
    fi

    printf "\n%s%s[OK]%s  Online repository configuration applied.%s\n" \
        "${CLR_BOLD}" "${CLR_GREEN}" "${CLR_RESET}" "${CLR_RESET}"
    printf "     Written at : %s\n" "${write_timestamp}"
}

# ==============================================================================
# SECTION 10 — SWITCH TO OFFLINE
# ==============================================================================

switch_to_offline() {
    log_step "Switching to OFFLINE Repositories"

    local write_timestamp
    write_timestamp="$(date --utc '+%Y-%m-%d %H:%M:%S UTC')"

    log_info "Backing up existing configuration files..."
    backup_file "${PACMAN_CONF}"
    backup_file "${MIRRORLIST_FILE}"

    log_info "Writing offline mirrorlist -> '${MIRRORLIST_FILE}'..."

    write_file_atomically "${MIRRORLIST_FILE}" << OFFLINE_MIRRORLIST_EOF
################################################################################
# /etc/pacman.d/mirrorlist — OFFLINE MODE
################################################################################
# Managed by: pacman_repo_switch.sh
# State:      OFFLINE — local installation media repository
# Written:    ${write_timestamp}
#
# Current offline repository URL:
#   ${OFFLINE_REPO_PATH}
#

Server = ${OFFLINE_REPO_PATH}
OFFLINE_MIRRORLIST_EOF

    log_info "Offline mirrorlist written successfully."
    log_info "Writing offline pacman.conf -> '${PACMAN_CONF}'..."

    write_file_atomically "${PACMAN_CONF}" << OFFLINE_PACMAN_CONF_EOF
# ==============================================================================
# /etc/pacman.conf — OFFLINE MODE
# ==============================================================================
# Managed by: pacman_repo_switch.sh
# State:      OFFLINE — local installation media repository
# Written:    ${write_timestamp}

[options]
Color
ILoveCandy
VerbosePkgLists
HoldPkg     = pacman glibc
Architecture = auto
CheckSpace
ParallelDownloads = 5
# DownloadUser = alpm

SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional

[${OFFLINE_REPO_NAME}]
SigLevel = Never
Include = ${MIRRORLIST_FILE}
OFFLINE_PACMAN_CONF_EOF

    log_info "Offline pacman.conf written successfully."
    log_step "Verifying Offline Repository"

    local repo_fs_path="${OFFLINE_REPO_PATH#file://}"
    local db_file="${repo_fs_path}/${OFFLINE_REPO_NAME}.db"

    if [[ ! -d "${repo_fs_path}" ]]; then
        log_warn "Offline repository directory not found: '${repo_fs_path}'"
        log_warn "This is expected if the installation media is not currently mounted."
        printf "\n%s%s[OK]%s  Offline repository configuration applied.%s\n" \
            "${CLR_BOLD}" "${CLR_GREEN}" "${CLR_RESET}" "${CLR_RESET}"
        return 0
    fi

    log_info "Offline repository directory found: '${repo_fs_path}'"

    if [[ ! -f "${db_file}" ]]; then
        log_warn "Database file not found: '${db_file}'"
        printf "\n%s%s[OK]%s  Offline repository configuration applied.%s\n" \
            "${CLR_BOLD}" "${CLR_GREEN}" "${CLR_RESET}" "${CLR_RESET}"
        return 0
    fi

    log_info "Database file confirmed: '${db_file}'"
    log_info "Syncing offline package database..."

    local pacman_exit=0
    pacman -Sy || pacman_exit=$?

    if (( pacman_exit == 0 )); then
        log_info "Offline package database synced successfully."
    else
        log_warn "'pacman -Sy' exited with code ${pacman_exit}."
    fi

    printf "\n%s%s[OK]%s  Offline repository configuration applied.%s\n" \
        "${CLR_BOLD}" "${CLR_GREEN}" "${CLR_RESET}" "${CLR_RESET}"
}

# ==============================================================================
# SECTION 11 — INTERACTIVE MENU
# ==============================================================================

show_menu() {
    printf "\n"
    printf "%s%s╔══════════════════════════════════════════╗%s\n" \
        "${CLR_BOLD}" "${CLR_CYAN}" "${CLR_RESET}"
    printf "%s%s║     Pacman Repository State Manager     ║%s\n" \
        "${CLR_BOLD}" "${CLR_CYAN}" "${CLR_RESET}"
    printf "%s%s╚══════════════════════════════════════════╝%s\n" \
        "${CLR_BOLD}" "${CLR_CYAN}" "${CLR_RESET}"
    printf "\n"
    printf "  Current script settings:\n"
    printf "    pacman.conf   : %s\n" "${PACMAN_CONF}"
    printf "    mirrorlist    : %s\n" "${MIRRORLIST_FILE}"
    printf "    Offline URL   : %s\n" "${OFFLINE_REPO_PATH}"
    printf "    Offline repo  : [%s]\n" "${OFFLINE_REPO_NAME}"
    printf "\n"
    printf "  %s[1]%s  Switch to %sOnline%s  — standard Arch Linux HTTPS mirrors\n" \
        "${CLR_BOLD}" "${CLR_RESET}" "${CLR_GREEN}" "${CLR_RESET}"
    printf "  %s[2]%s  Switch to %sOffline%s — local installation media (file://)\n" \
        "${CLR_BOLD}" "${CLR_RESET}" "${CLR_YELLOW}" "${CLR_RESET}"
    printf "  %s[q]%s  Quit — no changes will be made\n" \
        "${CLR_BOLD}" "${CLR_RESET}"
    printf "\n"

    local user_choice
    while true; do
        printf "  Your choice [1/2/q]: "

        if ! read -r -n1 -t 60 user_choice; then
            printf "\n"
            log_warn "No input received within 60 seconds. Quitting with no changes."
            exit 0
        fi

        printf "\n"

        case "${user_choice}" in
            1)
                switch_to_online
                return 0
                ;;
            2)
                switch_to_offline
                return 0
                ;;
            q|Q)
                log_info "Quit selected. No changes were made."
                exit 0
                ;;
            *)
                log_warn "Invalid choice: '${user_choice}'. Please enter 1, 2, or q."
                printf "\n"
                ;;
        esac
    done
}

# ==============================================================================
# SECTION 12 — USAGE / HELP
# ==============================================================================

show_usage() {
    printf "\n"
    printf "Usage: %s [OPTION]\n" "${BASH_SOURCE[0]}"
    printf "\n"
    printf "  --online    Write online HTTPS configuration and sync package databases.\n"
    printf "  --offline   Write offline local file:// configuration.\n"
    printf "  --help      Display this help text.\n"
    printf "  (no flag)   Launch the interactive menu.\n"
    printf "\n"
    printf "  Requires root. If not root, the script will attempt to re-launch\n"
    printf "  itself automatically using 'sudo'.\n"
    printf "\n"
}

# ==============================================================================
# SECTION 13 — ENTRY POINT
# ==============================================================================

main() {
    validate_config

    local mode="${1:-}"

    case "${mode}" in
        --online)
            switch_to_online
            ;;
        --offline)
            switch_to_offline
            ;;
        --help|-h)
            show_usage
            exit 0
            ;;
        "")
            show_menu
            ;;
        *)
            log_error "Unrecognised argument: '${mode}'"
            show_usage
            exit 1
            ;;
    esac
}

main "$@"
