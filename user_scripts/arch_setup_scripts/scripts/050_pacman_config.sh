#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Description: True atomic overwrite and rollback of /etc/pacman.conf
# Target:      /etc/pacman.conf
# Ecosystem:   Arch Linux / Hyprland / UWSM
#
# Supported Flags:
#   --auto, auto : Run in non-interactive mode. Bypasses user prompts.
#                  Silently aborts if CachyOS is detected to protect the OS.
#   --revert     : Bypasses generation. Atomically restores the pacman
#                  configuration from /etc/pacman.conf.bak.
#   --cachyos    : Force CachyOS target (Overrides auto-detection).
#   --arch       : Force Standard Arch target (Overrides auto-detection).
# -----------------------------------------------------------------------------

# --- Strict Error Handling ---
set -euo pipefail

# --- Presentation Constants (Bash 5+ ANSI Quoting) ---
readonly COLOR_INFO=$'\e[1;34m'
readonly COLOR_WARN=$'\e[1;33m'
readonly COLOR_OK=$'\e[1;32m'
readonly COLOR_ERR=$'\e[1;31m'
readonly COLOR_NC=$'\e[0m'

# --- Helper Functions ---
log_info() { printf "%s[INFO]%s %s\n" "${COLOR_INFO}" "${COLOR_NC}" "$1"; }
log_warn() { printf "%s[WARN]%s %s\n" "${COLOR_WARN}" "${COLOR_NC}" "$1"; }
log_ok()   { printf "%s[OK]%s   %s\n" "${COLOR_OK}" "${COLOR_NC}" "$1"; }
log_err()  { printf "%s[ERR]%s  %s\n" "${COLOR_ERR}" "${COLOR_NC}" "$1" >&2; }

# --- Cleanup Trap ---
cleanup() {
    local exit_code=$?

    # Safely remove temporary deployment, backup, or restore files if they exist
    [[ -n "${TMP_FILE:-}" && -f "${TMP_FILE}" ]] && rm -f "${TMP_FILE}"
    [[ -n "${TMP_RESTORE:-}" && -f "${TMP_RESTORE}" ]] && rm -f "${TMP_RESTORE}"
    [[ -n "${TMP_BACKUP:-}" && -f "${TMP_BACKUP}" ]] && rm -f "${TMP_BACKUP}"

    # Reset terminal colors on stdout and stderr, suppressing errors to preserve exit code
    printf "%s" "${COLOR_NC}" 2>/dev/null || true
    printf "%s" "${COLOR_NC}" >&2 2>/dev/null || true

    exit "${exit_code}"
}
trap cleanup EXIT

# --- 1. Root Privilege Check (Self-Elevation) ---
if [[ "${EUID}" -ne 0 ]]; then
    log_info "Privilege escalation required for /etc/pacman.conf operations."
    # Resolve absolute path to prevent execution failures under strict sudo policies
    exec sudo "$(realpath "$0")" "$@"
fi

# --- 2. Argument Parsing ---
AUTO_MODE=0
REVERT_MODE=0
TARGET_OS=""

for arg in "$@"; do
    case "${arg}" in
        --auto|auto)
            AUTO_MODE=1
            ;;
        --revert)
            REVERT_MODE=1
            ;;
        --cachyos|--cachy)
            TARGET_OS="cachyos"
            ;;
        --arch)
            TARGET_OS="arch"
            ;;
    esac
done

TARGET_FILE="/etc/pacman.conf"
TARGET_DIR="$(dirname "${TARGET_FILE}")"
BACKUP_FILE="${TARGET_FILE}.bak"

# --- 3. Rollback Logic (--revert) ---
if (( REVERT_MODE == 1 )); then
    log_info "Revert mode initiated."
    if [[ ! -f "${BACKUP_FILE}" ]]; then
        log_err "No backup found at ${BACKUP_FILE}. Cannot revert."
        exit 1
    fi

    log_info "Preparing atomic restoration from backup..."
    TMP_RESTORE="$(mktemp "${TARGET_DIR}/.pacman.conf.restore.XXXXXX")"

    # Copy backup to temp file preserving all attributes
    cp -a "${BACKUP_FILE}" "${TMP_RESTORE}"

    # Atomic rename
    if mv "${TMP_RESTORE}" "${TARGET_FILE}"; then
        log_ok "Successfully reverted ${TARGET_FILE} to previous state."
        exit 0
    else
        log_err "Failed to atomically restore backup."
        exit 1
    fi
fi

# --- 4. Organic State Intelligence ---
if [[ -z "${TARGET_OS}" ]]; then
    log_info "Analyzing system state to determine optimal configuration..."

    if grep -qi "ID=cachyos" /etc/os-release 2>/dev/null; then
        log_info "Pure CachyOS detected. CachyOS manages its own pacman configuration."
        log_info "Aborting pacman configuration update to preserve system integrity."
        exit 0
    elif pacman -Qq cachyos-mirrorlist &>/dev/null; then
        log_ok "Franken-Arch detected (CachyOS packages found on Standard Arch)."
        TARGET_OS="cachyos"
    else
        log_info "Standard Arch Linux detected."
        TARGET_OS="arch"
    fi
fi

# --- 5. Atomic Backup Current Configuration ---
if [[ -f "${TARGET_FILE}" ]]; then
    log_info "Creating atomic backup of current configuration..."
    TMP_BACKUP="$(mktemp "${TARGET_DIR}/.pacman.conf.bak.XXXXXX")"

    # Copy preserving attributes to temp file
    cp -a "${TARGET_FILE}" "${TMP_BACKUP}"

    # True atomic rename(2) for backup
    if mv "${TMP_BACKUP}" "${BACKUP_FILE}"; then
        log_ok "Backup safely saved to ${BACKUP_FILE}"
    else
        log_err "Failed to atomically create backup."
        exit 1
    fi
fi

# --- 6. Main Logic & True Atomic Write ---
# Create temp file on the SAME filesystem to guarantee rename(2) atomicity
TMP_FILE="$(mktemp "${TARGET_DIR}/.pacman.conf.XXXXXX")"

log_info "Generating new configuration for ${TARGET_OS^^}..."

{
    cat << 'EOF'
# /etc/pacman.conf
# See the pacman.conf(5) manpage for option and repository directives
[options]
# The following paths are commented out with their default values listed.
# If you wish to use different paths, uncomment and update the paths.

# Pacman won't upgrade packages listed in IgnorePkg and members of IgnoreGroup
#IgnorePkg   =
#IgnoreGroup =

#NoUpgrade   =
#NoExtract   =

# Misc options
Color
ILoveCandy
VerbosePkgLists
HoldPkg     = pacman glibc
CheckSpace
ParallelDownloads = 5
DownloadUser = alpm

# By default, pacman accepts packages signed by keys that its local keyring
# trusts (see pacman-key and its man page), as well as unsigned packages.
SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional
#RemoteFileSigLevel = Required

# NOTE: You must run `pacman-key --init` before first using pacman; the local
# keyring can then be populated with the keys of all official Arch Linux
# packagers with `pacman-key --populate archlinux`.

#
# REPOSITORIES
#   - can be defined here or included from another file
#   - pacman will search repositories in the order defined here
#   - local/custom mirrors can be added here or in separate files
#   - repositories listed first will take precedence when packages
#     have identical names, regardless of version number
#   - URLs will have $repo replaced by the name of the current repo
#   - URLs will have $arch replaced by the name of the architecture
#
# Repository entries are of the format:
#       [repo-name]
#       Server = ServerName
#       Include = IncludePath
#
# The header [repo-name] is crucial - it must be present and
# uncommented to enable the repo.
#

EOF

    # --- DYNAMIC CACHYOS INJECTION ---
    if [[ "${TARGET_OS}" == "cachyos" ]]; then
        cat << 'CACHYOS_BLOCK_EOF'
# Architecture must be set to "auto" for CachyOS repos on Franken-Arch.
# The mirrorlist files (installed by cachyos-v3-mirrorlist package) contain
# hardcoded x86_64_v3 paths — they do NOT rely on pacman's $arch variable.
# Using "x86_64_v3 x86_64" here causes pacman to request the wrong arch
# strings and triggers 404 errors.
Architecture = auto

# CachyOS Optimised Repositories for x86-64-v3
# Source: https://wiki.cachyos.org/features/optimized_repos/
# Installation methodology: https://github.com/CachyOS/cachyos-repo-add-script
#
# IMPORTANT REPO ORDER: Architecture-specific repos MUST come before [cachyos].
# Pacman resolves packages in repo order — v3-optimised packages must win.

[cachyos-v3]
# SigLevel inherits global "Required DatabaseOptional" — do NOT set Optional TrustAll.
Include = /etc/pacman.d/cachyos-v3-mirrorlist

[cachyos-core-v3]
Include = /etc/pacman.d/cachyos-v3-mirrorlist

[cachyos-extra-v3]
Include = /etc/pacman.d/cachyos-v3-mirrorlist

# WARNING: The [cachyos] repo contains CachyOS's forked pacman (with INSTALLED_FROM
# tracking and auto-arch detection). Pacman 6.1 added feature validation that can
# produce warnings when the standard Arch pacman reads packages built with the
# CachyOS fork. CachyOS themselves state: "If you want to avoid this, don't add the
# [cachyos] repository." On a Franken-Arch install this repo is included because
# the system already has cachyos-mirrorlist installed (that's how it was detected),
# but be aware of this trade-off. See: https://wiki.cachyos.org/features/optimized_repos/
[cachyos]
Include = /etc/pacman.d/cachyos-mirrorlist

CACHYOS_BLOCK_EOF
    else
        echo "Architecture = auto"
        echo ""
    fi
    # ---------------------------------

    cat << 'ARCH_BLOCK_EOF'
# The testing repositories are disabled by default. To enable, uncomment the
# repo name header and Include lines. You can add preferred servers immediately
# after the header, and they will be used before the default mirrors.

#[core-testing]
#Include = /etc/pacman.d/mirrorlist

[core]
Include = /etc/pacman.d/mirrorlist

#[extra-testing]
#Include = /etc/pacman.d/mirrorlist

[extra]
Include = /etc/pacman.d/mirrorlist

# If you want to run 32 bit applications on your x86_64 system,
# enable the multilib repositories as required here.

#[multilib-testing]
#Include = /etc/pacman.d/mirrorlist

[multilib]
Include = /etc/pacman.d/mirrorlist

# An example of a custom package repository.  See the pacman manpage for
# tips on creating your own repositories.
#[custom]
#SigLevel = Optional TrustAll
#Server = file:///home/custompkgs
ARCH_BLOCK_EOF

} > "${TMP_FILE}"

# Ensure correct ownership and permissions before moving
chmod 644 "${TMP_FILE}"
chown root:root "${TMP_FILE}"

# --- 7. Validation and Deployment ---
# Because TMP_FILE and TARGET_FILE are on the same filesystem, this is a true atomic rename(2)
if mv "${TMP_FILE}" "${TARGET_FILE}"; then
    log_ok "Configuration atomically written to ${TARGET_FILE}."
else
    log_err "Failed to move temporary file to ${TARGET_FILE}."
    exit 1
fi
